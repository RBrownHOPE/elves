# Faster trusted runs — learnings

- Quality should be expressed as evidence and invariants, not equal time quotas or repeated gates.
- Trusted same-user workers need outcome verification; untrusted writers need provenance proof.
- Driver activity after delegation is a defect unless a material safety, checkpoint, or terminal
  transition occurred.
- Cache computation is cheap; cross-commit invalidation and duplicate broad gates are the waste.

