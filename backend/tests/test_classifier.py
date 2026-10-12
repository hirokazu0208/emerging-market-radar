import pytest

from app.classifier import classify


@pytest.mark.parametrize(
    "title, expected",
    [
        ("OpenAI releases a new LLM for coding agents", "AI"),
        ("Solar and battery storage now cheaper than gas", "Climate & Energy"),
        ("CRISPR gene therapy approved by FDA", "Biotech & Health"),
        ("Stripe launches stablecoin payments", "Fintech & Crypto"),
        ("SpaceX Starship reaches orbit", "Space"),
        ("A new RISC-V chip for robots", "Robotics & Hardware"),
        ("EU passes new law on data regulation", "Policy & Regulation"),
        ("Show HN: A SQLite CLI written in Rust", "Developer Tools"),
        ("My grandmother's apple pie recipe", "Other"),
    ],
)
def test_classify(title, expected):
    assert classify(title) == expected


def test_keyword_must_be_whole_word():
    # "ai" が "said" や "maintain" の一部として誤検出されないこと
    assert classify("He said they maintain the garden") == "Other"
