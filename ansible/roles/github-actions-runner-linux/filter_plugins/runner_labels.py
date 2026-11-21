"""The custom labels a registered runner should carry, from what GitHub lists for it.

config.sh sets labels only when it registers a runner, so a label the inventory later
changes never reaches it (2026-11-12: aws-build-1's build slots kept unity, unity-full
and unity-gate). The role reads the runner, and when "change" is true, PUTs "labels" to
<api_path>/actions/runners/<id>/labels, which replaces every custom label at once.
"""


def runner_labels_plan(runner, desired, governed=(), withheld=None, withheld_all=(), kept=()):
    """{"change": whether runner (one row of GitHub's runners list) needs a PUT, "labels": the
    custom labels it should carry}. GitHub compares labels without regard to case.

    desired: the labels the inventory gives the runner. Its read-only ones (self-hosted,
    Linux, X64) stay out of the PUT, which only sets custom labels.
    governed, withheld, withheld_all: ci-runner-governor withholds a slot by swapping its
    governed labels (unity, unity-full, unity-build) for a withheld one. A runner that
    carries any of withheld_all stays withheld: it gets desired without the governed labels,
    plus withheld, the one the governor gives back on admit for this slot's kind. So an
    apply never admits a slot the governor holds, and the governor's admit still finds its
    own withheld label to drop.
    kept: labels only the governor sets (unity-lent, its lend mark): a runner carrying one
    keeps it, so an apply never takes back a slot the governor lent to a seat.
    """
    live = runner.get("labels", [])
    readonly = {l["name"].lower() for l in live if l.get("type") == "read-only"}
    have = {l["name"].lower() for l in live if l.get("type") != "read-only"}
    gov = {g.lower() for g in governed}
    held = bool(have & {w.lower() for w in withheld_all})
    if held and not withheld:
        raise ValueError(f"{runner.get('name')} is withheld but no withheld label was given for it")
    want, seen = [], set()
    marks = [l["name"] for l in live if l.get("type") != "read-only" and l["name"].lower() in {k.lower() for k in kept}]
    for label in list(desired) + ([withheld] if held else []) + marks:
        key = label.lower()
        if key in readonly or key in seen or (held and key in gov):
            continue
        seen.add(key)
        want.append(label)
    return {"change": seen != have, "labels": sorted(want, key=str.lower)}


class FilterModule:
    def filters(self):
        return {"runner_labels_plan": runner_labels_plan}
