"""Golden-set evaluation of the Help AI against the LIVE Anthropic API.

    python -m scripts.help_center.live_eval            # spends real money
    python -m scripts.help_center.live_eval --limit 10 # a cheaper sample

OPT-IN, NEVER IN CI. tests/help_center/ proves the CODE offline (retrieval
recall, the guards with FakeClaude, mock-mode precision). Only a real model
can show whether it actually answers from the documents and refuses what it
must -- which costs money per question, so this script:

  * exits 2 without ANTHROPIC_API_KEY (nothing is verified, nothing is spent);
  * exits 0 without running when CI is set -- it must never be a CI step;
  * aborts before grading if the key is dead (a 401 turns every answer into a
    ticket, which would score the must-refuse "ticket" items as passes --
    the false-proof trap scripts/support_chat_adversarial.py documents).

REPORTS
  answerable   answer rate (reason "answered"), ticket rate, and how often
               the model cited one of the item's expected chunks.
  must-refuse  how many ended on the expected path (refusal constant or
               ticket), and every one that got an answer instead.
  unsupported-sentence rate
               for every answered item, each sentence carrying a checkable
               detail -- a number, money or percentage, a proper noun, a
               quoted phrase -- is checked against the text of the documents
               the model was shown. A detail absent from them makes the
               sentence unsupported. This borrows the token-support rule and
               the unit-normalised number matching of the claim-verification
               engine (app/services/claim_verification.py). The engine itself
               does not fit: its evidence ledger is a LEAD row and its claim
               categories are claims about prospects, not product facts.
               Sentences with no checkable detail are counted separately --
               this metric cannot judge them, so it does not pretend to.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _guard() -> int | None:
    if os.environ.get("CI"):
        print("CI is set -- live_eval never runs in CI (it spends real money). Skipped.")
        return 0
    try:
        from dotenv import load_dotenv
        load_dotenv(REPO / ".env")
    except ImportError:
        pass
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        print("ANTHROPIC_API_KEY is not set -- cannot run a LIVE evaluation. "
              "NOTHING HAS BEEN VERIFIED.")
        return 2
    return None


def _unsupported_details(sentence: str, documents: str) -> list[str]:
    from app.services import claim_verification as cv

    docs_lower = documents.lower()
    doc_numbers = cv._numbers(documents)
    missing = []
    for number in cv._numbers(sentence):
        if not any(abs(number - known) <= max(1e-6, abs(known) * 0.005)
                   for known in doc_numbers):
            missing.append(f"number:{number:g}")
    for quote in cv._QUOTED.findall(sentence):
        if quote.strip().lower() not in docs_lower:
            missing.append(f"quote:{quote.strip()}")
    for match in cv._PROPER.finditer(sentence):
        at_start = match.start() == len(sentence) - len(sentence.lstrip())
        words = [cv._POSSESSIVE.sub("", w).strip("’'-") for w in match.group(1).split()]
        words = [w for w in words if w and w not in cv._STOP]
        if at_start and words:
            words = words[1:]           # sentence-initial capital is grammar
        phrase = " ".join(words)
        if len(phrase) >= 2 and phrase.lower() not in docs_lower:
            missing.append(f"name:{phrase}")
    return missing


def _checkable(sentence: str) -> bool:
    from app.services import claim_verification as cv
    return bool(cv._numbers(sentence) or cv._QUOTED.search(sentence)
                or any(len(m.group(1)) > 1 for m in cv._PROPER.finditer(sentence.strip()[1:])))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--limit", type=int, default=0,
                        help="only the first N items of each set (0 = all)")
    args = parser.parse_args(argv)

    stop = _guard()
    if stop is not None:
        return stop

    os.environ.setdefault("APP_ENV", "development")
    import yaml

    from app.config import settings
    from app.services import claim_verification as cv
    from app.services import help_center, support_chat

    settings.ai_mode = "live"
    golden = yaml.safe_load((REPO / "tests/help_center/golden_questions.yaml")
                            .read_text(encoding="utf-8"))
    answerable = golden["answerable"]
    refuse = golden["must_refuse"]
    if args.limit:
        answerable, refuse = answerable[:args.limit], refuse[:args.limit]

    print(f"model: {settings.anthropic_model}")
    probe = support_chat.answer_question("What is Vectorae?")
    if probe.reason == "model_error" or probe.reason.startswith("mock_"):
        print(f"ABORTED: the model is not usable (probe reason={probe.reason}). Every "
              "item would land on a ticket and the must-refuse 'ticket' items would "
              "score false passes. NOTHING HAS BEEN VERIFIED.")
        return 2

    answered = tickets = cited_expected = 0
    checkable = unsupported = uncheckable = 0
    unsupported_lines: list[str] = []
    print("\nANSWERABLE")
    for item in answerable:
        result = support_chat.answer_question(item["question"])
        if result.reason == "answered":
            answered += 1
            if set(result.faq_ids) & set(item["expected"]):
                cited_expected += 1
            documents = "\n".join(h.chunk.article_title + "\n" + h.chunk.body
                                  for h in help_center.retrieve(item["question"]))
            for sentence in (s.strip() for s in cv._SENTENCE.findall(result.text)):
                if not sentence:
                    continue
                if not _checkable(sentence):
                    uncheckable += 1
                    continue
                checkable += 1
                missing = _unsupported_details(sentence, documents)
                if missing:
                    unsupported += 1
                    unsupported_lines.append(f"  {item['id']}: {sentence!r} -> {missing}")
        if result.suggest_ticket:
            tickets += 1
        print(f"  {item['id']:<4} {result.reason:<16} cites={list(result.faq_ids)[:2]}")

    refuse_ok = 0
    refuse_fail: list[str] = []
    print("\nMUST-REFUSE")
    for item in refuse:
        result = support_chat.answer_question(item["question"])
        if item["expected_path"] == "refusal":
            ok = result.reason == "off_topic"
        else:
            ok = result.suggest_ticket and result.reason != "model_error"
        # Either safe path is acceptable; an ANSWER never is.
        safe = result.reason != "answered" and result.reason != "model_error"
        refuse_ok += 1 if safe else 0
        if not safe:
            refuse_fail.append(f"  {item['id']} {item['question']!r} -> answered: {result.text!r}")
        print(f"  {item['id']:<4} {result.reason:<16} expected={item['expected_path']:<8} "
              f"{'exact' if ok else ('safe' if safe else 'ANSWERED')}")

    n = len(answerable)
    print("\n" + "=" * 72)
    print(f"answer rate            {answered}/{n} ({answered / n:.1%})")
    print(f"ticket rate            {tickets}/{n} ({tickets / n:.1%})")
    print(f"cited an expected chunk {cited_expected}/{answered or 1}")
    print(f"must-refuse safe       {refuse_ok}/{len(refuse)}")
    rate = (unsupported / checkable) if checkable else 0.0
    print(f"unsupported sentences  {unsupported}/{checkable} checkable ({rate:.1%}); "
          f"{uncheckable} sentences had no checkable detail")
    if unsupported_lines:
        print("\nUNSUPPORTED:\n" + "\n".join(unsupported_lines))
    if refuse_fail:
        print("\nMUST-REFUSE ANSWERED:\n" + "\n".join(refuse_fail))
    return 1 if (refuse_fail or unsupported) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
