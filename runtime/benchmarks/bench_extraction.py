"""Fixed rule-extraction benchmark.

It measures with the real model, over a fixed sample of code chunks from an indexed project:
tokens read, cached and written, seconds and tokens per second per batch, and rules proposed.
Separately, without the model, it measures the filter: how much code would reach the model and
how many batches the whole project would need, and estimates the time from the measured speed.

- The sample does not depend on the filter or the score: it is the first N chunks by a fixed
  hash of their path and line. So changing the filter does not change what is measured here.
- It writes nothing: it opens the database read-only and does not touch the extraction queue.
- It starts its own llama-server with Scrinium's settings and stops it when done.
- Run it under the resource limit (`make bench`), never at the same time as Scrinium.

Quality (F1): with `--golden benchmarks/golden/reference-a.json` it measures filter recall
(no model) and extraction recall, and with `--sample golden` the sample is the chunks holding
those rules. Each rule goes through the `rule_checks` checks (cut title, language, UI,
evidence) and into `<out>.review.md` for review by hand.

Reading and writing (F8): with llama-server, each batch splits the time to read the input from
the time to write the output, and counts the tokens speculative decoding proposed and accepted.

Usage:
    uv run python benchmarks/bench_extraction.py --data-dir DIR --out report.json
        [--project NAME] [--fragments 60] [--with-embeddings] [--from-passing]
        [--golden FILE] [--sample hash|golden]
"""

import argparse
import hashlib
import json
import platform
import secrets
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from scrinium.application.analysis import extraction, rule_checks, rule_detail
from scrinium.application.analysis.candidates import (
    CANDIDATES_VERSION,
    DEFAULT_MIN_SCORE,
    Candidate,
    select_candidates,
)
from scrinium.cli.main import start_llama_server
from scrinium.infrastructure.llama_server import model_threads
from scrinium.infrastructure.llm.base import CONTEXT_TOKENS, TokenUsage
from scrinium.infrastructure.llm.load_order import ChatFirst
from scrinium.infrastructure.llm.openai_compatible import OpenAICompatibleChat
from scrinium.infrastructure.local_http import LocalHttpClient, ModelError
from scrinium.infrastructure.sqlite.models import Project
from scrinium.infrastructure.tools.manifest import CHAT_MODEL, EMBEDDING_MODEL, MAP_MODEL

DB_NAME = "scrinium.sqlite3"


def _sample_key(candidate: Candidate) -> str:
    return hashlib.sha256(
        f"{candidate.path}:{candidate.start_line}:{candidate.part}".encode()
    ).hexdigest()


def _fragment(candidate: Candidate) -> extraction.Fragment:
    return extraction.Fragment(
        0, candidate.revision_id, candidate.path, candidate.start_line, candidate.end_line,
        candidate.text, candidate.symbol,
    )  # fmt: skip


def _filter_stats(session: Session, project: Project) -> dict[str, Any]:
    everything = select_candidates(session, project.id, None, min_score=_EVERYTHING)
    passing = [c for c in everything.candidates if c.score >= DEFAULT_MIN_SCORE]
    total_chars = sum(len(c.text) for c in everything.candidates)
    passing_chars = sum(len(c.text) for c in passing)
    ordered = sorted(passing, key=lambda c: -c.score)
    return {
        "functions_total": everything.functions_total,
        "parts_total": len(everything.candidates),
        "parts_passing": len(passing),
        "chars_total": total_chars,
        "chars_passing": passing_chars,
        "code_share_passing": round(passing_chars / total_chars, 3) if total_chars else 0,
        "batches_full_project": len(extraction.batches([_fragment(c) for c in ordered])),
        "min_score": DEFAULT_MIN_SCORE,
    }


def _overlaps(path: str, start: int, end: int, rule: dict[str, Any]) -> bool:
    return bool(path == rule["path"] and start <= rule["end"] and rule["start"] <= end)


def _golden_filter(golden: list[dict[str, Any]], everything: list[Candidate]) -> dict[str, Any]:
    """Filter recall: reference rules that fall in a chunk that passes. No model."""
    passing = [c for c in everything if c.score >= DEFAULT_MIN_SCORE]
    kept = [g["id"] for g in golden if any(_overlaps(c.path, c.start_line, c.end_line, g)
                                           for c in passing)]  # fmt: skip
    return {
        "rules": len(golden),
        "filter_kept": len(kept),
        "filter_recall": round(len(kept) / len(golden), 3) if golden else None,
        "filter_lost": [g["id"] for g in golden if g["id"] not in kept],
    }


def _rule_row(rule: extraction.ValidRule) -> dict[str, Any]:
    """An extracted rule with its checks (F1), for the report and the review."""
    cited = [
        rule_checks.cited_text(fragment.text, fragment.start_line, start, end)
        for fragment, start, end in rule.citations
    ]
    fragment, start, end = rule.citations[0]
    context = rule_detail.context_excerpt(
        fragment.text.split("\n"), fragment.start_line, start, end
    )
    title, statement = rule.content.title, rule.content.statement
    return {
        "title": title,
        "statement": statement,
        "category": rule.category.value,
        "citations": [[f.path, start, end] for f, start, end in rule.citations],
        "checks": {
            "truncated_title": rule_checks.is_truncated(title, extraction.TITLE_MAX_CHARS),
            "language": rule_checks.language(f"{title} {statement}"),
            "visual": rule_checks.mentions_visual(f"{title} {statement}"),
            "evidence": all(rule_checks.has_evidence(text) for text in cited),
        },
        "cited_text": cited,
        # What phase 2 sees (S3): the cited lines and a few lines around them.
        "context": context,
    }


def _quality(rows: list[dict[str, Any]], golden: list[dict[str, Any]] | None,
             sent: list[extraction.Fragment]) -> dict[str, Any]:  # fmt: skip
    total = len(rows)
    checks = [row["checks"] for row in rows]
    quality: dict[str, Any] = {
        "rules": total,
        "truncated_titles": sum(c["truncated_title"] for c in checks),
        "language": {
            lang: sum(c["language"] == lang for c in checks) for lang in ("es", "en", "?")
        },
        "visual": sum(c["visual"] for c in checks),
        "without_evidence": sum(not c["evidence"] for c in checks),
    }
    if golden is not None:
        reachable = [
            g for g in golden if any(_overlaps(f.path, f.start_line, f.end_line, g) for f in sent)
        ]
        found = [g["id"] for g in reachable
                 if any(_overlaps(path, start, end, g)
                        for row in rows for path, start, end in row["citations"])]  # fmt: skip
        quality.update(
            golden_in_sample=len(reachable),
            golden_found=len(found),
            extraction_recall=round(len(found) / len(reachable), 3) if reachable else None,
            golden_missed=[g["id"] for g in reachable if g["id"] not in found],
        )
    return quality


def _review(path: Path, rows: list[dict[str, Any]]) -> None:
    """Template for review by hand: one rule per block, with its cited lines."""
    out = ["# Revision de reglas extraidas", "",
           "Marca cada regla: `[x] valida` si es una regla de negocio que el codigo citado "
           "respalda; si no, deja `[ ]` y anota por que.", ""]  # fmt: skip
    for number, row in enumerate(rows, 1):
        flags = [name for name, bad in (
            ("titulo cortado", row["checks"]["truncated_title"]),
            ("interfaz", row["checks"]["visual"]),
            ("sin evidencia", not row["checks"]["evidence"]),
            (f"idioma {row['checks']['language']}", row["checks"]["language"] != "es"),
        ) if bad]  # fmt: skip
        out += [f"## {number}. {row['title']}", "", f"- [ ] valida — {row['category']}"
                + (f" — avisos: {', '.join(flags)}" if flags else ""), "",
                row["statement"], ""]  # fmt: skip
        for (source, start, end), text in zip(row["citations"], row["cited_text"], strict=True):
            lines = text.split("\n")
            shown = "\n".join(lines[:15]) + ("\n…" if len(lines) > 15 else "")
            out += [f"`{source}:{start}-{end}`", "", "```", shown, "```", ""]
    path.write_text("\n".join(out))


def _phase_two(chat: OpenAICompatibleChat, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """S3: the statements of the map's rules, as in `_write_statements`, without a
    database. Puts the statement in each row and measures what it costs."""
    items = [
        rule_detail.DetailItem(f"R{n}", row["title"], row["citations"][0][0], row["context"])
        for n, row in enumerate(rows, 1)
    ]
    seconds = written = read = 0.0
    failed = 0
    for start in range(0, len(items), rule_detail.DETAIL_BATCH):
        group = items[start : start + rule_detail.DETAIL_BATCH]
        began = time.monotonic()
        try:
            completion = chat.complete(
                rule_detail.build_detail_messages(group, secrets.token_hex(8)),
                rule_detail.output_tokens(group), rule_detail.detail_schema(group),
            )  # fmt: skip
            statements = rule_detail.interpret_details(completion.text, group)
        except ModelError:
            failed += 1
            continue
        finally:
            seconds += time.monotonic() - began
        read += completion.prompt_ms / 1000
        written += completion.generation_ms / 1000
        for item in group:
            row = rows[int(item.ref[1:]) - 1]
            row["statement"] = statements.get(item.ref, row["statement"])
            row["checks"]["language"] = rule_checks.language(f"{row['title']} {row['statement']}")
    batches = -(-len(items) // rule_detail.DETAIL_BATCH)
    print(f"fase 2: {len(items)} enunciados en {batches} lotes, {seconds:.1f} s", flush=True)
    return {
        "rules": len(items), "batches": batches, "batches_failed": failed,
        "seconds": round(seconds, 1), "read_seconds": round(read, 1),
        "write_seconds": round(written, 1),
        "seconds_per_rule": round(seconds / len(items), 2) if items else 0.0,
    }  # fmt: skip


def _unload(client: LocalHttpClient, model: str, seconds: float = 120) -> None:
    """Unloads a model from the router and waits until it shows as unloaded."""
    client.request("/models/unload", {"model": model}, 30)
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        listing: Any = client.request("/models", None, 30)
        states = {
            e.get("id"): (e.get("status") or {}).get("value") for e in listing.get("data", [])
        }
        if states.get(model) != "loaded":
            return
        time.sleep(0.5)


BENCH_SEED = 1
# All the code, including what the filter leaves out (V2 has negative scores): the
# fixed sample does not depend on the filter, so before and after runs are comparable.
_EVERYTHING = -1_000_000


def _load_embeddings(url: str, api_key: str) -> None:
    """As in real use after indexing: the embedding model also takes up the GPU."""
    client = LocalHttpClient(url, api_key)
    client.request("/v1/embeddings", {"model": EMBEDDING_MODEL.name, "input": "hola"}, 120)


def run(args: argparse.Namespace) -> dict[str, Any]:
    database = args.data_dir / DB_NAME
    engine = create_engine(f"sqlite:///file:{database}?mode=ro&uri=true")
    with Session(engine) as session:
        query = select(Project).order_by(Project.created_at)
        if args.project:
            query = query.where(Project.name == args.project)
        project = session.scalars(query).first()
        if project is None:
            sys.exit("Proyecto no encontrado.")
        stats = _filter_stats(session, project)
        everything = select_candidates(session, project.id, None, min_score=_EVERYTHING).candidates
    golden = json.loads(args.golden.read_text())["rules"] if args.golden else None
    if golden is not None:
        stats["golden"] = _golden_filter(golden, everything)
    if args.from_passing:
        # What really reaches the model with the current filter.
        everything = [c for c in everything if c.score >= DEFAULT_MIN_SCORE]
    if args.sample == "golden":
        if golden is None:
            sys.exit("--sample golden necesita --golden.")
        # The chunks holding reference rules, whether or not they pass: measures extraction alone.
        everything = [
            c
            for c in everything
            if any(_overlaps(c.path, c.start_line, c.end_line, g) for g in golden)
        ]
    sample = sorted(everything, key=_sample_key)[: args.fragments]
    groups = extraction.batches([_fragment(c) for c in sample])

    llama = start_llama_server(args.data_dir)
    if llama is None or llama.url is None:
        sys.exit("No arranco llama-server.")
    try:
        if args.with_embeddings:
            _load_embeddings(llama.url, llama.api_key)
        # As in real use (`local_models` in main.py): chat loads before the
        # embeddings. With --with-embeddings already loaded, it unloads them first (V4).
        local = LocalHttpClient(llama.url, llama.api_key)
        # The map (phase 1) with the small model if asked; the statements with the chat model.
        map_name = MAP_MODEL.name if args.map_model else CHAT_MODEL.name
        order = ChatFirst(local, map_name, EMBEDDING_MODEL.name)
        # Temperature 0 and a fixed seed: the same input gives the same output, so two runs
        # can be compared (at 0.2 the output varied by 20 % from one run to the next).
        chat = OpenAICompatibleChat(
            llama.url, map_name, llama.api_key, before_request=order.before_chat,
            temperature=0.0, seed=BENCH_SEED,
        )  # fmt: skip
        if args.with_embeddings:
            # And they load again after chat, as with the next search: both on the
            # GPU, with chat whole.
            order.before_chat()
            _load_embeddings(llama.url, llama.api_key)
        batches: list[dict[str, Any]] = []
        rows: list[dict[str, Any]] = []
        usage = TokenUsage()
        for number, group in enumerate(groups, 1):
            nonce = secrets.token_hex(8)
            messages = extraction.build_messages(group, nonce)
            started = time.monotonic()
            row: dict[str, Any] = {
                "batch": number,
                "fragments": len(group),
                "input_chars": sum(len(m.content) for m in messages),
            }
            try:
                completion = chat.complete(
                    messages, extraction.output_tokens(group), extraction.extraction_schema(group)
                )
            except ModelError as error:
                row.update(error=type(error).__name__, seconds=round(time.monotonic() - started, 1))
                batches.append(row)
                print(f"lote {number}/{len(groups)}: error {row['error']}", flush=True)
                continue
            seconds = time.monotonic() - started
            usage.add(completion)
            try:
                rules, dropped = extraction.interpret(completion.text, group)
            except ModelError:
                rules, dropped = [], -1
            read = completion.prompt_ms / 1000
            written = completion.generation_ms / 1000
            row.update(
                seconds=round(seconds, 1),
                input_tokens=completion.prompt_tokens,
                cached_tokens=completion.cached_tokens,
                output_tokens=completion.completion_tokens,
                # The whole batch: mixes reading and writing.
                output_tokens_per_second=round(completion.completion_tokens / seconds, 1),
                read_seconds=round(read, 2),
                write_seconds=round(written, 2),
                read_tokens_per_second=round(
                    (completion.prompt_tokens - completion.cached_tokens) / read, 1
                ) if read else None,
                write_tokens_per_second=round(completion.completion_tokens / written, 1)
                if written else None,
                draft_tokens=completion.draft_tokens,
                draft_accepted=completion.draft_accepted,
                decisions=len(extraction.decisions(group)),
                rules=len(rules),
                dropped_rules=dropped,
                rule_titles=[rule.content.title for rule in rules],
            )  # fmt: skip
            rows += [_rule_row(rule) for rule in rules]
            batches.append(row)
            print(
                f"lote {number}/{len(groups)}: {row['seconds']} s "
                f"(leer {row['read_seconds']} s, escribir {row['write_seconds']} s), "
                f"{row['input_tokens']} leidos ({row['cached_tokens']} de cache), "
                f"{row['output_tokens']} escritos, {row['rules']} reglas",
                flush=True,
            )
        if args.map_model:
            # Unload the map model: the chat model needs the GPU for the statements (V4).
            _unload(local, MAP_MODEL.name)
            order = ChatFirst(local, CHAT_MODEL.name, EMBEDDING_MODEL.name)
            chat = OpenAICompatibleChat(
                llama.url, CHAT_MODEL.name, llama.api_key, before_request=order.before_chat,
                temperature=0.0, seed=BENCH_SEED,
            )  # fmt: skip
        details = _phase_two(chat, rows)
    finally:
        llama.stop()

    ok = [b for b in batches if "error" not in b]
    seconds = sum(b["seconds"] for b in ok)
    per_batch = seconds / len(ok) if ok else 0
    read = sum(b["read_seconds"] for b in ok)
    written = sum(b["write_seconds"] for b in ok)
    fresh = sum(b["input_tokens"] - b["cached_tokens"] for b in ok)
    drafted = sum(b["draft_tokens"] for b in ok)
    summary = {
        "batches": len(groups),
        "batches_failed": len(groups) - len(ok),
        "seconds_total": round(seconds, 1),
        "seconds_per_batch": round(per_batch, 1),
        "tokens": usage.as_dict(),
        "output_tokens_per_batch": round(usage.output / len(ok)) if ok else 0,
        "read_seconds_per_batch": round(read / len(ok), 1) if ok else 0,
        "write_seconds_per_batch": round(written / len(ok), 1) if ok else 0,
        "read_tokens_per_second": round(fresh / read, 1) if read else None,
        "write_tokens_per_second": round(usage.output / written, 1) if written else None,
        "draft_acceptance": round(sum(b["draft_accepted"] for b in ok) / drafted, 3)
        if drafted
        else None,
        "decisions": sum(b["decisions"] for b in ok),
        "rules": sum(b["rules"] for b in ok),
        "map_minutes_full_project": round(stats["batches_full_project"] * per_batch / 60, 1),
        "detail": details,
    }
    # S3: the map of the whole project, then the statements of its rules (the same
    # rules per batch as in the sample).
    rules_full = summary["rules"] / len(ok) * stats["batches_full_project"] if ok else 0
    detail_minutes = rules_full * details["seconds_per_rule"] / 60
    summary["detail_minutes_full_project"] = round(detail_minutes, 1)
    summary["estimated_full_project_minutes"] = round(
        summary["map_minutes_full_project"] + detail_minutes
    )
    return {
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "project": project.name,
        "machine": {"platform": platform.platform(), "model_threads": model_threads()},
        "settings": {
            "chat_model": asdict(CHAT_MODEL),
            "context_tokens": CONTEXT_TOKENS,
            "prompt_version": extraction.PROMPT_VERSION,
            "candidates_version": CANDIDATES_VERSION,
            "batch_chars": extraction.BATCH_CHARS,
            "max_output_tokens": extraction.MAX_OUTPUT_TOKENS,
            "max_decisions_per_batch": extraction.MAX_DECISIONS_PER_BATCH,
            "sample_fragments": len(sample),
            "with_embeddings_loaded": args.with_embeddings,
            "from_passing": args.from_passing,
            "temperature": 0.0,
            "seed": BENCH_SEED,
            "sample": args.sample,
            "golden": str(args.golden) if args.golden else None,
            "map_model": asdict(MAP_MODEL) if args.map_model else None,
        },
        "filter": stats,
        "summary": summary,
        "quality": _quality(rows, golden, [f for group in groups for f in group]),
        "batches_detail": batches,
        "rules_detail": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--project")
    parser.add_argument("--fragments", type=int, default=60)
    parser.add_argument("--with-embeddings", action="store_true")
    parser.add_argument(
        "--from-passing", action="store_true", help="muestra solo de lo que pasa el filtro"
    )
    parser.add_argument("--golden", type=Path, help="reglas de referencia (F1)")
    parser.add_argument("--sample", choices=["hash", "golden"], default="hash")
    parser.add_argument(
        "--map-model", action="store_true", help="el mapa (fase 1) con el modelo pequeno"
    )
    args = parser.parse_args()
    report = run(args)
    report_path: Path = args.out
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    review_path = report_path.with_suffix(".review.md")
    _review(review_path, report["rules_detail"])
    s, f, q = report["summary"], report["filter"], report["quality"]
    print(
        f"\n{s['batches']} lotes ({s['batches_failed']} fallidos), "
        f"{s['seconds_per_batch']} s/lote, "
        f"{s['output_tokens_per_batch']} tokens escritos/lote, {s['rules']} reglas.\n"
        f"Tokens: {s['tokens']['input']} leidos ({s['tokens']['cached']} de cache), "
        f"{s['tokens']['output']} escritos.\n"
        f"Filtro: llega al modelo el {f['code_share_passing']:.0%} del codigo; "
        f"{f['batches_full_project']} lotes para el proyecto entero, "
        f"unos {s['estimated_full_project_minutes']} min (mapa {s['map_minutes_full_project']}"
        f" + enunciados {s['detail_minutes_full_project']}).\n"
        f"Lectura {s['read_seconds_per_batch']} s/lote ({s['read_tokens_per_second']} tokens/s), "
        f"escritura {s['write_seconds_per_batch']} s/lote ({s['write_tokens_per_second']} "
        f"tokens/s), aceptacion especulativa {s['draft_acceptance']}.\n"
        f"Calidad: {q['truncated_titles']} titulos cortados, idioma {q['language']}, "
        f"{q['visual']} de interfaz, {q['without_evidence']} sin evidencia."
    )
    if "golden" in f:
        print(f"Recall del filtro: {f['golden']['filter_recall']} "
              f"(perdidas: {', '.join(f['golden']['filter_lost']) or 'ninguna'}).")  # fmt: skip
    if "extraction_recall" in q:
        print(f"Recall de la extraccion: {q['extraction_recall']} "
              f"({q['golden_found']} de {q['golden_in_sample']} en la muestra).")  # fmt: skip
    print(f"Informe: {report_path}\nRevision: {review_path}")


if __name__ == "__main__":
    main()
