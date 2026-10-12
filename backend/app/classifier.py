"""タイトルのキーワードによる簡易カテゴリー分類。"""

import re

OTHER = "Other"

# 一致したキーワード数が最も多いカテゴリーを採用する（同数なら上にあるものを優先）。
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "AI": [
        "ai", "llm", "llms", "gpt", "openai", "anthropic", "claude", "gemini", "chatgpt",
        "machine learning", "deep learning", "neural", "agent", "agents", "transformer",
        "diffusion", "inference", "model", "models", "rag", "copilot",
    ],
    "Climate & Energy": [
        "climate", "energy", "solar", "battery", "batteries", "nuclear", "fusion",
        "carbon", "emissions", "renewable", "grid", "ev", "hydrogen", "wind",
    ],
    "Biotech & Health": [
        "biotech", "health", "medical", "drug", "drugs", "gene", "genome", "crispr",
        "cancer", "fda", "vaccine", "protein", "clinical", "brain", "disease",
    ],
    "Fintech & Crypto": [
        "fintech", "bank", "banking", "payment", "payments", "crypto", "bitcoin",
        "ethereum", "blockchain", "stablecoin", "finance", "stripe", "trading",
    ],
    "Space": [
        "space", "spacex", "nasa", "rocket", "satellite", "satellites", "orbit",
        "mars", "moon", "lunar", "starship",
    ],
    "Robotics & Hardware": [
        "robot", "robots", "robotics", "drone", "drones", "chip", "chips",
        "semiconductor", "gpu", "gpus", "hardware", "risc-v", "fpga", "humanoid",
    ],
    "Policy & Regulation": [
        "law", "regulation", "regulator", "regulators", "ban", "bill", "court",
        "lawsuit", "antitrust", "ftc", "eu", "gdpr", "policy", "congress", "senate",
    ],
    "Developer Tools": [
        "api", "sdk", "open source", "open-source", "github", "framework", "compiler",
        "database", "rust", "python", "javascript", "typescript", "golang", "linux",
        "kubernetes", "postgres", "sqlite", "cli", "programming",
    ],
}

_PATTERNS: dict[str, re.Pattern[str]] = {
    category: re.compile(
        r"(?<![a-z0-9])(" + "|".join(re.escape(k) for k in keywords) + r")(?![a-z0-9])"
    )
    for category, keywords in CATEGORY_KEYWORDS.items()
}

CATEGORIES: list[str] = [*CATEGORY_KEYWORDS, OTHER]


def classify(title: str) -> str:
    text = title.lower()
    best, best_hits = OTHER, 0
    for category, pattern in _PATTERNS.items():
        hits = len(pattern.findall(text))
        if hits > best_hits:
            best, best_hits = category, hits
    return best
