"""Validate a batch result file against its input batch.

Usage:
    python tools/check_batch.py runs/<SID>/<step>-results/batch-NNN.json

Derives the matching input batch path by swapping `-results` -> `-batches`,
then checks:
  - result file is valid JSON
  - contains a list of dicts with an `id` field
  - covers every id in the input batch exactly once (no missing, no extras, no dups)
  - for summarize-style outputs: every `short_summary` is <=25 words

Exits 0 on OK, 1 on validation failure, 2 on usage error.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

MAX_SHORT_SUMMARY_WORDS = 25


def find_results_list(obj, id_field: str):
    if isinstance(obj, list):
        if obj and isinstance(obj[0], dict) and id_field in obj[0]:
            return obj
        return None
    if isinstance(obj, dict):
        for v in obj.values():
            if isinstance(v, list) and v and isinstance(v[0], dict) and id_field in v[0]:
                return v
    return None


def detect_id_field(batch: dict) -> str:
    """Infer the id-field name from the input batch's items/clusters list.

    Returns the field name in the input items whose values match the batch's
    `ids` list. Falls back to 'id'. This matches the field name the result
    schema expects (e.g. cluster_id for name_topic_batch, id for filter/summarize).
    """
    expected = {str(i) for i in batch.get("ids", [])}
    if not expected:
        return "id"
    for v in batch.values():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            sample = v[0]
            for field, val in sample.items():
                if str(val) in expected:
                    return field
    return "id"


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: check_batch.py <batch_result.json>", file=sys.stderr)
        return 2

    result_path = Path(sys.argv[1])
    if not result_path.is_file():
        print(f"FAIL: result file not found: {result_path}", file=sys.stderr)
        return 1

    # Derive input batch path: .../<step>-results/batch-NNN.json -> .../<step>-batches/batch-NNN.json
    parent = result_path.parent
    if not parent.name.endswith("-results"):
        print(
            f"FAIL: expected result path under a *-results/ directory, got {parent}",
            file=sys.stderr,
        )
        return 1
    batch_dir = parent.parent / parent.name.replace("-results", "-batches")
    batch_path = batch_dir / result_path.name
    if not batch_path.is_file():
        print(f"FAIL: input batch not found at {batch_path}", file=sys.stderr)
        return 1

    try:
        batch = json.loads(batch_path.read_text())
    except json.JSONDecodeError as e:
        print(f"FAIL: input batch is not valid JSON: {e}", file=sys.stderr)
        return 1

    try:
        result = json.loads(result_path.read_text())
    except json.JSONDecodeError as e:
        print(f"FAIL: result is not valid JSON: {e}")
        return 1

    expected_ids = [str(i) for i in batch.get("ids", [])]

    # ── select-repechage result shape: {proposed_clusters, unclustered_ids} ──
    if (
        isinstance(result, dict)
        and "proposed_clusters" in result
        and "unclustered_ids" in result
    ):
        errors: list[str] = []
        proposed = result.get("proposed_clusters")
        unclustered = result.get("unclustered_ids")
        if not isinstance(proposed, list):
            errors.append("'proposed_clusters' must be a list")
            proposed = []
        if not isinstance(unclustered, list):
            errors.append("'unclustered_ids' must be a list")
            unclustered = []
        seen_ids: list[str] = []
        names_seen: set[str] = set()
        for i, c in enumerate(proposed):
            if not isinstance(c, dict):
                errors.append(f"proposed_clusters[{i}] must be an object")
                continue
            name = c.get("name")
            members = c.get("member_ids")
            if not isinstance(name, str) or not name:
                errors.append(f"proposed_clusters[{i}].name must be a non-empty string")
            elif name in names_seen:
                errors.append(f"proposed_clusters[{i}].name duplicate: {name!r}")
            else:
                names_seen.add(name)
            if not isinstance(members, list) or len(members) < 2:
                errors.append(
                    f"proposed_clusters[{i}].member_ids must be a list of >=2 ids"
                )
                members = []
            seen_ids.extend(str(m) for m in members)
        seen_ids.extend(str(u) for u in unclustered)
        if expected_ids:
            expected_set = set(expected_ids)
            got_set = set(seen_ids)
            missing = sorted(expected_set - got_set)
            extras = sorted(got_set - expected_set)
            dups = sorted({i for i in seen_ids if seen_ids.count(i) > 1})
            if missing:
                errors.append(f"missing ids ({len(missing)}): {missing}")
            if extras:
                errors.append(f"extra ids ({len(extras)}): {extras}")
            if dups:
                errors.append(f"duplicate ids ({len(dups)}): {dups}")
        if errors:
            print("FAIL (repechage):")
            for e in errors:
                print(f"  - {e}")
            return 1
        print(
            f"OK (repechage): {len(proposed)} cluster(s), "
            f"{len(unclustered)} unclustered, "
            f"{len(seen_ids)}/{len(expected_ids)} ids covered"
        )
        return 0

    # ── select-consolidate result shape: {final_names, mapping} ──
    if (
        isinstance(result, dict)
        and "final_names" in result
        and "mapping" in result
    ):
        errors = []
        final_names = result.get("final_names")
        mapping = result.get("mapping")
        if not isinstance(final_names, list) or not all(
            isinstance(n, str) and n for n in final_names
        ):
            errors.append("'final_names' must be a list of non-empty strings")
            final_names_set: set[str] = set()
        else:
            if "Other" in final_names:
                errors.append("'final_names' must not contain 'Other'")
            if len(set(final_names)) != len(final_names):
                errors.append("'final_names' has duplicates")
            final_names_set = set(final_names)
        if not isinstance(mapping, list):
            errors.append("'mapping' must be a list")
            mapping = []
        seen_cluster_ids: list[str] = []
        for i, m in enumerate(mapping):
            if not isinstance(m, dict):
                errors.append(f"mapping[{i}] must be an object")
                continue
            cid = m.get("cluster_id")
            final = m.get("final_name")
            if not isinstance(cid, str) or not cid:
                errors.append(f"mapping[{i}].cluster_id must be a non-empty string")
            else:
                seen_cluster_ids.append(cid)
            if not isinstance(final, str) or not final:
                errors.append(f"mapping[{i}].final_name must be a non-empty string")
            elif final != "Other" and final not in final_names_set:
                errors.append(
                    f"mapping[{i}].final_name {final!r} is not in final_names "
                    f"(and is not 'Other')"
                )
        if expected_ids:
            expected_set = set(expected_ids)
            got_set = set(seen_cluster_ids)
            missing = sorted(expected_set - got_set)
            extras = sorted(got_set - expected_set)
            dups = sorted({i for i in seen_cluster_ids if seen_cluster_ids.count(i) > 1})
            if missing:
                errors.append(f"missing cluster_ids ({len(missing)}): {missing}")
            if extras:
                errors.append(f"extra cluster_ids ({len(extras)}): {extras}")
            if dups:
                errors.append(f"duplicate cluster_ids ({len(dups)}): {dups}")
        if errors:
            print("FAIL (consolidate):")
            for e in errors:
                print(f"  - {e}")
            return 1
        print(
            f"OK (consolidate): {len(final_names) if isinstance(final_names, list) else 0} "
            f"final name(s), {len(mapping)}/{len(expected_ids)} input cluster_ids mapped"
        )
        return 0

    if not expected_ids:
        # Draft batches have no 'ids'; validate as a single DraftSectionResult object.
        required_draft_fields = {
            "cat", "final_section_markdown", "iterations", "scores", "feedbacks", "accepted"
        }
        if isinstance(result, dict) and required_draft_fields.issubset(result.keys()):
            errors = []
            if not isinstance(result["cat"], str) or not result["cat"]:
                errors.append("'cat' must be a non-empty string")
            if not isinstance(result["final_section_markdown"], str) or not result["final_section_markdown"]:
                errors.append("'final_section_markdown' must be a non-empty string")
            if not isinstance(result["iterations"], int) or result["iterations"] < 1:
                errors.append("'iterations' must be a positive integer")
            if not isinstance(result["scores"], list) or not result["scores"]:
                errors.append("'scores' must be a non-empty list")
            if not isinstance(result["feedbacks"], list) or not result["feedbacks"]:
                errors.append("'feedbacks' must be a non-empty list")
            if not isinstance(result["accepted"], bool):
                errors.append("'accepted' must be a boolean")
            if len(result["scores"]) != result["iterations"]:
                errors.append(
                    f"'scores' length ({len(result['scores'])}) must equal 'iterations' ({result['iterations']})"
                )
            if len(result["feedbacks"]) != result["iterations"]:
                errors.append(
                    f"'feedbacks' length ({len(result['feedbacks'])}) must equal 'iterations' ({result['iterations']})"
                )
            if errors:
                print("FAIL (draft result):")
                for e in errors:
                    print(f"  - {e}")
                return 1
            print(
                f"OK: draft result for cat='{result['cat']}', "
                f"iterations={result['iterations']}, accepted={result['accepted']}"
            )
            return 0
        print("FAIL: input batch has no 'ids' field", file=sys.stderr)
        return 1

    id_field = detect_id_field(batch)
    results_list = find_results_list(result, id_field)
    if results_list is None:
        print(
            f"FAIL: could not find a list of dicts with '{id_field}' fields in "
            f"result (the result schema for this batch requires '{id_field}', "
            f"not 'id'). Re-write the file using '{id_field}' as the key name.",
            file=sys.stderr,
        )
        return 1

    got_ids = [str(r.get(id_field)) for r in results_list]
    expected_set = set(expected_ids)
    got_set = set(got_ids)

    missing = sorted(expected_set - got_set)
    extras = sorted(got_set - expected_set)
    dups = sorted({i for i in got_ids if got_ids.count(i) > 1})

    long_short_summaries = []
    for r in results_list:
        ss = r.get("short_summary")
        if isinstance(ss, str):
            n = len(ss.split())
            if n > MAX_SHORT_SUMMARY_WORDS:
                long_short_summaries.append((r.get("id"), n))

    issues = []
    if missing:
        issues.append(f"missing ids ({len(missing)}): {missing}")
    if extras:
        issues.append(f"extra ids ({len(extras)}): {extras}")
    if dups:
        issues.append(f"duplicate ids ({len(dups)}): {dups}")
    if long_short_summaries:
        issues.append(
            f"short_summary >{MAX_SHORT_SUMMARY_WORDS} words: {long_short_summaries}"
        )

    if issues:
        print("FAIL:")
        for i in issues:
            print(f"  - {i}")
        return 1

    print(
        f"OK: {len(results_list)}/{len(expected_ids)} ids covered, "
        f"no schema or word-count issues"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
