from operator import truediv
import os
import re
import git
import yaml
import logging

build_path = [
    '/mnt/ral-rdgbuild-03/DI_APPSERVICES/NightlyBuilds/Latest'
    ]


def get_gittop():
    repo = git.Repo('.', search_parent_directories=True)
    GITTOP=repo.working_tree_dir
    return GITTOP

def get_zip(dir):
    files = os.listdir(dir)
    r = re.compile("DI-AppServices-Package.*zip")
    di = list(filter(r.match, files)) # Read Note below

    # not sure how this would be true, maybe prod signed??
    assert len(di) == 1
    return os.path.join(dir, di[0])

def get_build(version=None, release=False, logger=None):
    found = None
    fast_lookup = {}

    if not logger:
        logger = logging.getLogger()

    cache_file = os.path.join(get_gittop(), ".di_versions_cache.yaml")
    if os.path.exists(cache_file):
        with open(cache_file, "r") as f:
            fast_lookup = yaml.safe_load(f)

    dirs = os.listdir(build_path[0])
    prefix=build_path[0]
    suffix = "bionic-x86_64/TargetRelease/FinalPackage" if release else "bionic-x86_64/TargetDebug/FinalPackage"

    # find all matching versions
    matches = [value for key, value in fast_lookup.items() if re.match(version, key)]
    if matches:
        if len(matches) > 1:
            logger.warning("many versions matching %s.  Returning the last entry", version)
        found = matches[-1]
    else:
        # could not find version in cache, so scan the directories (slow)
        for entry in dirs:
            if entry in fast_lookup.values():
                continue
            try:
                path = os.path.join(prefix, entry, suffix)
                files = os.listdir(path)
                r = re.compile("DI-AppServices-Package.*zip")
                di = list(filter(r.match, files)) # Read Note below
                if di:
                    filename = di[0]
                    ver=re.search("Package-([0123456789.]+)_[TtPp][sS]", filename)
                    fver = ver[1]
                    fullname = os.path.join(path, filename)
                    if fver in fast_lookup.keys():
                        logger.error("multiple directories with same version: %s", version)
                        logger.error("  %s and %s", fast_lookup[fver], entry)
                    fast_lookup[fver] = entry
                    if re.match(version, fver):
                        found = fullname
                        break
            except FileNotFoundError:
                pass

        with open(cache_file, "w", ) as f:
            yaml.dump(fast_lookup, f, default_flow_style=False,indent=True)

    return get_zip(os.path.join(prefix, found, suffix)) if found else None
