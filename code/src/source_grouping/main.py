# -*- coding: utf-8 -*-
from urllib.parse import urlsplit
from sqlalchemy import update
from fuzzywuzzy import fuzz
from mana_common.shared import log, set_logger, flush_logs
from mana_common.orm import Entity, get_session, merge_entities
import os

# ----------------------------------------------------------------------------------------
# /!\ Important: do NOT log (=print) outside of Python functions, this will lead to action
#                failure when deployed on IBM Cloud (using Cloud Functions)
# ----------------------------------------------------------------------------------------


# +-------------+
# | Global vars |
# +-------------+
FUZZY_MATCH_SUGGESTED_THRESHOLD = 82


# +-----------+
# | Functions |
# +-----------+

def is_auto_match_urls(url1, url2):
    if url1 is None or url2 is None:
        return False

    # First compare base path
    split_url1 = urlsplit(url1)
    split_url2 = urlsplit(url2)

    if split_url1.netloc.lower() != split_url2.netloc.lower():
        return False

    if not split_url1.path and not split_url2.path:
        return True

    return split_url1.path.lower() == split_url2.path.lower()


def is_auto_match(e1, e2):
    match = False
    reason = None
    twitter_matched = False

    for o1 in e1["origins"]:
        for o2 in e2["origins"]:

            if o1["type"] == "rss_origin" or o2["type"] == "rss_origin":
                # RSS are not used for matching at this stage
                continue

            if o1["type"] == o2["type"]:
                # Same origin types
                if o1["type"] == "twitter_origin":
                    # Comparing twitter origins
                    reason = "Same Twitter screen name: {}".format(o1["screen_name"])
                    match = (o1["screen_name"] == o2["screen_name"])
                    twitter_matched = match
                else:
                    # Comparing web origins
                    reason = "Same Web origin: {}".format(o1["expanded_url"])
                    match = is_auto_match_urls(o1["expanded_url"], o2["expanded_url"])

            else:
                # Comparing web origin & twitter origin
                to, wo = (o1, o2) if o1["type"] == 'twitter_origin' else (o2, o1)
                reason = "Same Twitter profile URL & Web origin: {}".format(to["twitter_profile_url"])
                match = is_auto_match_urls(to["twitter_profile_url"], wo["expanded_url"])

            if match:
                break

    return match, reason, twitter_matched


def is_suggested_match(e1, e2):
    match = False
    reason = None

    for o1 in e1["origins"]:
        for o2 in e2["origins"]:

            # RSS are not used for matching at this stage
            if o1["type"] == "rss_origin" or o2["type"] == "rss_origin":
                continue

            if o1["type"] == o2["type"]:
                if o1["type"] == "twitter_origin":
                    match = is_suggested_match_strings(o1["screen_name"], o2["screen_name"])
                    reason = "Similar Twitter screen names: {} ~ {}".format(o1["screen_name"], o2["screen_name"])
                else:
                    match = is_suggested_match_urls(o1["base_url"], o2["base_url"])
                    reason = "Similar Web origins: {} ~ {}".format(o1["base_url"], o2["base_url"])
            else:
                to, wo = (o1, o2) if o1["type"] == 'twitter_origin' else (o2, o1)
                match = is_suggested_match_urls(to["twitter_profile_url"], wo["base_url"])
                reason = "Similar Twitter profile URL & Web origin: {} ~ {}".format(to["twitter_profile_url"],
                                                                                    wo["base_url"])
            if match:
                break

    return match, reason


def is_suggested_match_strings(s1, s2):
    if s1 is None or s2 is None:
        return False
    score = fuzz.token_set_ratio(s1.lower(), s2.lower())
    return score >= FUZZY_MATCH_SUGGESTED_THRESHOLD


def is_suggested_match_urls(url1, url2):
    if url1 is None or url1 is None:
        return False

    # First compare base path
    split_url1 = urlsplit(url1)
    split_url2 = urlsplit(url2)

    score_netloc = fuzz.token_set_ratio(split_url1.netloc.lower(), split_url2.netloc.lower())
    if score_netloc < FUZZY_MATCH_SUGGESTED_THRESHOLD:
        return False

    if not split_url1.path and not split_url2.path:
        return True

    return fuzz.token_set_ratio(split_url1.path.lower(), split_url2.path.lower()) >= FUZZY_MATCH_SUGGESTED_THRESHOLD


# Group entities
def get_repartition_entities():
    entities_matched = []
    entities_to_match = []
    all_entities_results = get_session().query(Entity).all()
    nb_of_entities = len(all_entities_results)
    log.info(f"Obtained {nb_of_entities} entities")
    i = 1
    for e in all_entities_results:
        nb_of_origins = len(e.origins)
        log.info(f"Processing entity {i} of {nb_of_entities} (has {nb_of_origins} origins)")
        origins = []
        for o in e.origins:

            origin = {"type": o.type}
            if o.type == "twitter_origin":
                origin["screen_name"] = o.screen_name
                origin["twitter_profile_url"] = o.twitter_profile.url if o.twitter_profile is not None else None
            elif o.type == "web_origin":
                origin["expanded_url"] = o.expanded_url
                origin["base_url"] = o.base_url
            origins.append(origin)

        entity = {"id": e.id, "name": e.name, "is_reference": e.is_reference, "origins": origins}
        if e.match_done:
            entities_matched.append(entity)
        else:
            entities_to_match.append(entity)
        i += 1

    log.info(
        "Entities already matched: {}; entities to match: {}".format(len(entities_matched), len(entities_to_match))
    )

    return entities_matched, entities_to_match


def match_entity(entity, entities_matched, entities_to_match):
    log.info("Match entity id {}".format(entity["id"]))
    auto_match_entities = []
    suggested_match_entities = []

    def try_to_match(e1, e2):

        if e1["is_reference"] and e2["is_reference"]:
            # Do not attempt to merge references
            return

        if "auto_matched" in e1 or "auto_matched" in e2:
            # If any of the entity is auto matched then nothing to do
            return

        is_am, reason_am, twitter_matched_am = is_auto_match(e1, e2)
        if is_am:
            if twitter_matched_am:
                # auto match only if same Twitter account
                log.info("Auto match found: {} = {} [reason = {}]".format(e1["id"], e2["id"], reason_am))
                e1["auto_matched"] = True
                e2["auto_matched"] = True
                auto_match_entities.append({"e1": e1, "e2": e2, "reason": reason_am})
            else:
                log.info("Suggested match found: {} = {} [reason = {}]".format(e1["id"], e2["id"], reason_am))
                suggested_match_entities.append({"e1": e1, "e2": e2, "reason": reason_am})
        else:
            # We'll to suggested match only if no auto matched was found
            is_sm, reason_sm = is_suggested_match(e1, e2)
            if is_sm:
                log.info("Suggested match found: {} = {} [reason = {}]".format(e1["id"], e2["id"], reason_sm))
                suggested_match_entities.append({"e1": e1, "e2": e2, "reason": reason_sm})

    try:
        # Step 1 of 2: match already matched & new entities
        total = len(entities_matched)
        log.info("Step 1 of 2 - max {} operation(s) expected".format(total))
        for em in entities_matched:
            try_to_match(entity, em)

        # Step 2 of 2: match new entities together
        total = len(entities_to_match)
        log.info("Step 2 of 2 - max {} operation(s) expected".format(total))

        for em in entities_to_match:
            try_to_match(entity, em)

        log.info("Entities to merge (auto): {}".format(len(auto_match_entities)))
        for auto_match in auto_match_entities:
            merge_entities(auto_match["e1"]["id"], auto_match["e2"]["id"], auto_match["reason"])

        log.info("Entities that could be merged (suggested): {}".format(len(suggested_match_entities)))
        for suggested_match in suggested_match_entities:
            suggested_merge_entities(suggested_match["e1"]["id"], suggested_match["e2"]["id"],
                                     suggested_match["reason"])

        log.info("Set match_done to true for entity id {}".format(entity["id"]))
        stmt = update(Entity).where(Entity.id == entity["id"]). \
            values(match_done=True)
        get_session().execute(stmt)
        log.info("Update done")

    finally:
        get_session().commit()


def suggested_merge_entities(e1_id, e2_id, reason):
    # Fetch real entities
    entity1 = get_session().query(Entity).get(e1_id)
    entity2 = get_session().query(Entity).get(e2_id)

    if entity1 is None or entity2 is None:
        log.info("Cannot merge entities {} and {} as one of them does not exist anymore".format(e1_id, e2_id))
        return

    if entity1.is_reference and entity2.is_reference:
        log.info("Cannot merge entities {} and {} as they are both references".format(e1_id, e2_id))
        return

    if entity1.suggested_merges is None:
        entity1.suggested_merges = []

    if entity2.suggested_merges is None:
        entity2.suggested_merges = []

    log.info("e1={}".format(entity1.suggested_merges))
    log.info("e2={}".format(entity2.suggested_merges))

    if not any(i["id"] == e2_id for i in entity1.suggested_merges):
        entity1.suggested_merges.append({"id": e2_id, "reason": reason})

    if not any(i["id"] == e1_id for i in entity2.suggested_merges):
        entity2.suggested_merges.append({"id": e1_id, "reason": reason})


def main():
    try:
        set_logger("sg")
        log.info("==Source Grouping==")

        if os.getenv("SKIP_SI"):
            log.info("Skipping step as SKIP_SI is set")
            return
        elif os.getenv("SKIP_SG"):
            log.info("Skipping step as SKIP_SG is set")
            return

        entities_matched, entities_to_match = get_repartition_entities()
        while len(entities_to_match) > 0:
            entity = entities_to_match.pop()
            match_entity(entity, entities_matched, entities_to_match)
        log.info("Normal end of processing - completed")
    finally:
        # Flush logs to make sure we've got them all before leaving
        flush_logs()


# Run if local (Docker)
if __name__ == "__main__":
    main()
