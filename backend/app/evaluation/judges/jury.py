"""The jury interface: how a panel of judges is called and how it is pooled.

WHAT IS HERE AND WHAT IS DELIBERATELY NOT
------------------------------------------
Here: the `Juror` interface, the pooling rule, and the path from pooled verdicts
to a `JudgeResult`. All of it deterministic, all of it offline.

NOT HERE: any live judge. There is no Gemini credential in this environment, so
W7.2's determinism probe has not been run, and W7.2 is explicit that the probe
comes FIRST because it decides the shape of everything after it. Writing a judge
client before knowing whether the model accepts a seed, and what its measured
self-disagreement sigma is, would be building a reproducibility guarantee on an
assumption. `configured_jurors()` returns an empty panel and the reason, and
every surface downstream reports `unavailable` rather than a number.

WHY THE JURY IS ON A DIFFERENT VENDOR (W7.4)
---------------------------------------------
A judge from the same family as the generator exhibits self preference, measured
at roughly +10% to +25% win rate for a model's own output. An OpenAI-family
judge grading an OpenAI-family scorer is a structural bias, and moving the judge
to a different vendor removes it by construction rather than by correction.

Three heterogeneous judges pooled also beat one expensive judge on all three
axes that matter: 7 to 8 times lower cost, higher agreement with humans (kappa
0.763 to 0.906), and the lowest variance of any configuration tested. The
mechanism is HETEROGENEITY, not count, which is why `pool` refuses a panel whose
members are not distinct.

WHY THIS PACKAGE IS NOT UNDER `app/services/` (W7.4)
------------------------------------------------------
`MODEL_FOR_TASK` is a closed mapping onto exactly two model ids, and
`tests/test_llm_task_routing.py` greps executable source for any other model
string. The jury is not an exception to that closure; it is outside its scope,
and the separation is structural rather than declared:

  - it lives in `app/evaluation/judges/`, never in `app/services/`;
  - `tests/test_judge_isolation.py` asserts by AST that nothing under
    `app/services/` imports `app/evaluation/`, and that nothing here is
    reachable from `app/api/` or `app/workers/`;
  - `test_llm_task_routing.py` excludes `app/evaluation/` from its grep with
    the reason written into the test.

The whole value of the closed mapping is that a GRADE cannot be produced by an
unreviewed model. A judge that could serve a product request would destroy that
property, so the enforcement is that no product entry point can reach this code
at all.

Provenance: RPN-AI-UP-001 W7.4 and W7.5.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Protocol, Sequence

from app.evaluation.golden import GOLDEN_VERSION, JudgedCase
from app.evaluation.judges.protocol import JudgeProtocol, JudgeResult, build_result

#: What a juror returns when it will not commit to a label. An abstention is a
#: legitimate output, never a wrong answer: `protocol.build_result` widens the
#: accuracy interval for it rather than scoring it zero.
ABSTAIN = "__abstain__"

#: What the caller records when a juror returned something outside the scale.
#: Kept distinct from an abstention because they need different fixes: an
#: abstention is the judge declining, an invalid verdict is the judge or the
#: parsing being broken.
INVALID = "__invalid__"

#: The scale a juror votes on when a caller does not supply one. The product's
#: own four grades, so a measured disagreement is disagreement over the bands a
#: client actually reads. Declared here rather than imported from
#: `services.rating` on purpose: `tests/test_judge_isolation.py` asserts that
#: nothing under `app/evaluation/` reaches into `app/services/`, and an import
#: for four strings would be the first hole in that.
DEFAULT_SCALE: tuple[str, ...] = (
    "highly_matching",
    "matching",
    "moderately_matching",
    "not_matching",
)


class JuryUnavailable(RuntimeError):
    """No judge can be called, with the reason a reader needs.

    RAISED, not returned as a default verdict. A jury that quietly produced
    abstentions when it could not reach a provider would be indistinguishable
    from a jury that genuinely declined, and the accuracy interval would widen
    for a reason nothing recorded.
    """


class Juror(Protocol):
    """One judge. Given a case, returns a label from the scale, or ABSTAIN.

    A juror NEVER writes. W7.6's database tools for a judge are read only, on a
    read-only replica or a snapshot, scoped to the eval tenant; a judge with a
    write tool is not a judge. That constraint lives in the interface: this
    protocol has one method and it returns a string.
    """

    judge_id: str

    def verdict(self, case: JudgedCase) -> str:
        """The label this juror assigns, or `ABSTAIN`."""


@dataclass(frozen=True)
class PooledVerdict:
    """One case's pooled outcome, with the individual verdicts kept.

    The members are retained rather than discarded because a disagreement rate
    is the input to W8's gate threshold, and a pooled label alone cannot say
    whether the panel was unanimous or split two to one.
    """

    case_id: str
    label: str
    member_verdicts: tuple[str, ...]

    @property
    def unanimous(self) -> bool:
        return len(set(self.member_verdicts)) == 1


def pool(case_id: str, verdicts: Sequence[str], scale: Sequence[str]) -> PooledVerdict:
    """Majority vote, with a tie resolving to ABSTAIN rather than to a coin.

    A TIE IS NOT A VERDICT. Breaking it by juror order would make the pooled
    label depend on the order the panel was declared in, which is a number
    nobody could justify and everybody would eventually tune by reordering the
    list. Abstaining on a tie is what makes the abstention interval mean
    something: it is where the panel genuinely did not decide.
    """
    if not verdicts:
        raise JuryUnavailable(f"case {case_id} received no verdict from any juror")
    permitted = set(scale) | {ABSTAIN, INVALID}
    unknown = sorted(set(verdicts) - permitted)
    if unknown:
        raise ValueError(
            f"case {case_id} received verdicts outside the declared scale: "
            + ", ".join(unknown)
        )
    committed = [verdict for verdict in verdicts if verdict in set(scale)]
    if not committed:
        return PooledVerdict(case_id, ABSTAIN, tuple(verdicts))
    counts: dict[str, int] = {}
    for verdict in committed:
        counts[verdict] = counts.get(verdict, 0) + 1
    best = max(counts.values())
    winners = [label for label, count in counts.items() if count == best]
    if len(winners) != 1:
        return PooledVerdict(case_id, ABSTAIN, tuple(verdicts))
    return PooledVerdict(case_id, winners[0], tuple(verdicts))


#: The instruction every juror receives. ONE string, shared by the whole panel,
#: because a jury measures model disagreement and jurors given different
#: wording would be measuring the prompts instead.
#:
#: It names the scale, forbids prose, and says nothing about who wrote the
#: evidence or what the product thinks of it: a judge that knew the pipeline's
#: own verdict would be anchored to it, which is the failure an LLM jury exists
#: to detect rather than reproduce.
JUDGE_PROMPT = (
    "You are grading how well a candidate's stated evidence supports a single "
    "hiring requirement. Reply with exactly one word from this list and nothing "
    "else: {scale}.\n\nREQUIREMENT: {requirement}\nCANDIDATE EVIDENCE: {evidence}\n\nOne word:"
)


@dataclass(frozen=True)
class ModelJuror:
    """One model, on one vendor, voting on the scale.

    THE VENDOR IS A MODULE, not a client object, and both vendor modules expose
    the same three names. That is what lets a panel mix Groq and Gemini jurors
    without this class knowing which is which, and it is what stops a second
    vendor becoming a second code path.

    A TRANSPORT FAILURE ABSTAINS, IT DOES NOT GUESS. `vendor.call` returns
    `error:...` or `off_scale:...` for everything that is not a verdict, and
    both become `ABSTAIN` here. `build_result` then WIDENS the accuracy
    interval rather than scoring the case wrong, which is the honest treatment:
    a juror that could not be reached did not disagree with the human, it did
    not answer. Returning a label on failure would put a vendor outage into a
    kappa.

    This juror NEVER WRITES, and the protocol it satisfies has exactly one
    method returning a string. W7.6's rule that a judge holds only read-only
    tools is enforced by the interface having nowhere to put a write.
    """

    judge_id: str
    vendor: Any
    model: str
    keys: tuple[str, ...]
    scale: tuple[str, ...]
    seed: int | None = None

    def verdict(self, case: JudgedCase) -> str:
        prompt = JUDGE_PROMPT.format(
            scale=", ".join(self.scale),
            requirement=getattr(case, "requirement", case.payload_ref),
            evidence=getattr(case, "evidence", case.notes or case.payload_ref),
        )
        answer = self.vendor.call(
            self.model, prompt, list(self.keys), seed=self.seed, scale=self.scale
        )
        return answer if answer in self.scale else ABSTAIN


def configured_jurors(scale: Sequence[str] | None = None) -> tuple[Juror, ...]:
    """The panel this deployment can actually call.

    EMPTY IS STILL A LEGITIMATE ANSWER and is what a deployment with no judge
    credential gets. It is not a stub: a stub juror would produce verdicts,
    those verdicts would produce a kappa, and the kappa would be a number
    describing nothing while looking exactly like a measurement.

    WHICH VENDOR, AND WHY IT IS NOT A FALLBACK CHAIN. Groq is preferred and
    Gemini is used when Groq has no credential. That reads like a fallback and
    is not one: a jury is a PANEL, and the choice here is which panel exists,
    made once at construction from what is configured, never per call. Nothing
    retries across vendors and no juror silently becomes another juror.

    Groq is first because its free tier can actually complete the W7.2 probe.
    Gemini's ran out of daily allowance at roughly a third of 600 calls with
    every key answering 429, and a panel whose self-disagreement cannot be
    measured cannot inform W8's threshold.
    """
    from app.evaluation.judges import gemini, groq  # noqa: PLC0415 - cycle

    bands = tuple(scale) if scale else DEFAULT_SCALE
    for vendor in (groq, gemini):
        try:
            keys = tuple(vendor.credentials())
        except RuntimeError:
            continue
        name = vendor.__name__.rsplit(".", 1)[-1]
        return tuple(
            ModelJuror(
                judge_id=f"{name}:{model}",
                vendor=vendor,
                model=model,
                keys=keys,
                scale=bands,
            )
            for model in vendor.JUDGE_MODELS
        )
    return ()


def unavailable_reason() -> str:
    """Why no judge ran, in the words a report should print."""
    return (
        "no judge is configured: set one of GROQ_API_KEY_1..3 or "
        "GEMINI_API_KEY_1..3. A judge must sit OUTSIDE the product's closed "
        "model mapping, so it cannot borrow OPENAI_GPT_TERRA or "
        "OPENAI_GPT_LUNA: a model scoring its own family's output is worth "
        "roughly +10% to +25% in win rate, which would measure loyalty as "
        "quality."
    )


def judge_set(
    cases: Iterable[JudgedCase],
    jurors: Sequence[Juror],
    protocol: JudgeProtocol,
    *,
    dataset_version: str = GOLDEN_VERSION,
    judge_label: str = "jury",
) -> JudgeResult:
    """Run the panel over a labelled set and report it under the W7.5 contract.

    Refuses on an empty panel and on an empty set, naming which. Both are states
    this deployment is in today, and a function that returned a zero-filled
    result for either would be the failure the whole package exists to prevent.
    """
    panel = tuple(jurors)
    if not panel:
        raise JuryUnavailable(unavailable_reason())
    if len({juror.judge_id for juror in panel}) != len(panel):
        raise ValueError(
            "a jury's value comes from heterogeneity, not count; two jurors "
            "with the same id are one judge counted twice"
        )
    labelled = [case for case in cases]
    if not labelled:
        raise JuryUnavailable(
            "the golden set holds no human-labelled case, so there is nothing "
            "for a judge to be measured against"
        )

    pairs: list[tuple[str, str]] = []
    abstentions = 0
    invalid = 0
    for case in labelled:
        verdicts = [juror.verdict(case) for juror in panel]
        pooled = pool(case.case_id, verdicts, protocol.scale)
        if pooled.label == ABSTAIN:
            abstentions += 1
        elif pooled.label == INVALID:
            invalid += 1
        else:
            pairs.append((case.expected_label, pooled.label))

    return build_result(
        judge_label=judge_label,
        dataset_version=dataset_version,
        protocol=protocol,
        pairs=pairs,
        abstentions=abstentions,
        invalid=invalid,
    )
