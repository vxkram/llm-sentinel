def estimate_tokens(text: str) -> int:
    """Rough ~4-chars-per-token estimate, used only for pre-flight TPM
    reservation before a real token count is known. Reconciled against actual
    usage once the provider responds.
    """
    return max(1, (len(text) + 3) // 4)
