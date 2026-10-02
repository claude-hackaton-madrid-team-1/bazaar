"""The runtime LLM layer: Jev picks the model, the model talks (`ask`), writes words, and steers style.

An LLM never sets a price or authorizes a trade. Prices are structured and set by code, every
write still passes `guardrails.check()`, and every LLM failure falls back to the existing path.
"""
