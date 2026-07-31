type ValidationIssue = {
  loc?: unknown;
  msg?: unknown;
};

function humanizeField(value: unknown): string {
  if (!Array.isArray(value)) return "Request";
  const path = value
    .filter((part) => part !== "body")
    .map(String)
    .join(".");
  return path || "Request";
}

export function apiErrorMessage(error: unknown): string {
  if (
    !error ||
    typeof error !== "object" ||
    !("status" in error) ||
    !("detail" in error)
  ) {
    return error instanceof Error ? error.message : "Something went wrong.";
  }

  const payload =
    error.detail && typeof error.detail === "object"
      ? (error.detail as { detail?: unknown }).detail
      : undefined;
  if (Array.isArray(payload)) {
    const issues = payload
      .filter((item): item is ValidationIssue => Boolean(item && typeof item === "object"))
      .map((item) => `${humanizeField(item.loc)}: ${String(item.msg || "Invalid value")}`);
    if (issues.length) return issues.join(" · ");
  }
  if (typeof payload === "string") return payload;
  if (payload && typeof payload === "object" && "message" in payload) {
    return String((payload as { message: unknown }).message);
  }
  return error instanceof Error ? error.message : "Request failed.";
}
