"""Picks the GitHub runners a host no longer runs, from the .runner files under one home."""
import base64
import json
import posixpath
from urllib.parse import urlparse


def runners_retired(slurped, home, keep_names, keep_dirs):
    """slurped: the results of slurping each <home>/<dir>/.runner. A runner is kept when its
    name is in keep_names or its dir in keep_dirs (a kept seat whose .runner still has an older
    name). Returns {"retire": [{name, id, dir, unit, api_path}], "unreadable": [path]}.
    A .runner with no agentName (a Forgejo seat's) is not a GitHub runner and is skipped. One
    that cannot be read, or that is not in a direct child dir of home, is never retired: it is
    listed in "unreadable" for the apply to warn about."""
    home = posixpath.normpath(home)
    keep = {n.lower() for n in keep_names}
    kept_dirs = {posixpath.normpath(d) for d in keep_dirs}
    retire, unreadable = [], []
    for r in slurped:
        src = r.get("source") or r.get("item", {}).get("path", "?")
        try:
            reg = json.loads(base64.b64decode(r["content"]).decode("utf-8-sig"))
            name = reg.get("agentName")
            if not name:
                continue
            parts = urlparse(reg["gitHubUrl"]).path.strip("/").split("/")
            if len(parts) not in (1, 2):
                raise ValueError(reg["gitHubUrl"])
            agent_id = int(reg["agentId"])
        except (KeyError, TypeError, ValueError, AttributeError):
            unreadable.append(src)
            continue
        d = posixpath.dirname(posixpath.normpath(src))
        if posixpath.dirname(d) != home:
            unreadable.append(src)
            continue
        if name.lower() in keep or d in kept_dirs:
            continue
        api_path = f"orgs/{parts[0]}" if len(parts) == 1 else f"repos/{parts[0]}/{parts[1]}"
        retire.append({"name": name, "id": agent_id, "dir": d,
                       "unit": f"actions.runner.{name}.service", "api_path": api_path})
    return {"retire": sorted(retire, key=lambda x: x["name"]), "unreadable": sorted(unreadable)}


class FilterModule:
    def filters(self):
        return {"runners_retired": runners_retired}
