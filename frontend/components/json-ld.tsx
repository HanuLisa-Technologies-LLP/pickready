/**
 * Structured data, emitted as one script tag.
 *
 * WHY `dangerouslySetInnerHTML` IS THE CORRECT TOOL HERE, AND NOT AN XSS
 * VECTOR. React escapes text children, and inside a `<script>` element that
 * escaping produces `&quot;` where the parser needs `"`, so a JSON-LD payload
 * rendered as a child is valid HTML and invalid JSON, silently. The payload
 * this component receives is built in code from server data and serialised by
 * `JSON.stringify`, which escapes every quote, backslash and control character
 * in the values. The one sequence that would matter, a literal `</script>`
 * arriving inside a string, is escaped below so a value can never close the
 * tag it sits in.
 *
 * A builder returning `null` renders nothing. That is the contract every
 * caller relies on: a field with no data is ABSENT from the payload rather
 * than present and empty, because an empty `hiringOrganization` is a claim
 * that the job has no employer.
 */

/** A structured-data payload. Values come from server responses. */
export type JsonLdPayload = Record<string, unknown>;

/**
 * `<` is escaped to its unicode form. Inside a JSON string that is the same
 * character to any parser, and it means no value can spell `</script>`.
 */
function serialise(data: JsonLdPayload): string {
  return JSON.stringify(data).replace(/</g, "\\u003c");
}

export function JsonLd({ data }: { data: JsonLdPayload | null }) {
  if (!data) return null;
  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: serialise(data) }}
    />
  );
}

/**
 * Drops keys whose value is null, undefined, an empty string or an empty
 * array, so a caller can write the whole shape and let absence do the work.
 * Nothing is invented and nothing is defaulted.
 */
export function compact(data: JsonLdPayload): JsonLdPayload {
  const out: JsonLdPayload = {};
  for (const [key, value] of Object.entries(data)) {
    if (value === null || value === undefined) continue;
    if (typeof value === "string" && value.trim() === "") continue;
    if (Array.isArray(value) && value.length === 0) continue;
    out[key] = value;
  }
  return out;
}
