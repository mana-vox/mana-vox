# -*- coding: utf-8 -*-

from sqlalchemy import or_, and_
from mana_common.shared import log, set_logger, flush_logs
from mana_common.orm import EntityStatus, Entity, get_session, flag_source_candidate, \
    flag_trusted_candidate, enrich_source
import os

# ----------------------------------------------------------------------------------------
# /!\ Important: do NOT log (=print) outside of Python functions, this will lead to action
#                failure when deployed on IBM Cloud (using Cloud Functions)
# ----------------------------------------------------------------------------------------


# +-----------+
# | Functions |
# +-----------+

# Figure out which entities are SOURCE_CANDIDATES after the last run
def flag_source_candidates():
    entities = find_source_candidates_entities()

    try:
        for e in entities:
            flag_source_candidate(e)
    finally:
        get_session().commit()


# TODO - Should rather be a view
def enrich_sources():
    query = get_session().query(Entity).filter(Entity.status == EntityStatus.SOURCE_CANDIDATE)
    entities = query.all()
    log.info("There is(are) {} entity(ies) to enrich".format(len(entities)))
    try:
        for e in entities:
            enrich_source(e)
    finally:
        get_session().commit()


def find_source_candidates_entities():
    query = get_session().query(Entity).filter(Entity.status == EntityStatus.ENTITY)
    candidates_entities = query.all()
    log.info("Source candidates entities will be searched from {} entity(ies)".format(
        len(candidates_entities)))
    return candidates_entities


# Figure out which entities are SOURCE_CANDIDATES after the last run
def flag_trusted_candidates():
    entities = find_trusted_sources_candidates_entities()
    try:
        for e in entities:
            flag_trusted_candidate(e)
    finally:
        get_session().commit()


def find_trusted_sources_candidates_entities():
    query = get_session().query(Entity).filter(and_(or_(
        Entity.status == EntityStatus.ENTITY,
        Entity.status == EntityStatus.SOURCE_CANDIDATE,
        Entity.status == EntityStatus.SOURCE
    ), Entity.trusted is False))

    candidates_entities = query.all()
    log.info(
        "Trusted sources candidates entities will be searched from {} entity(ies)".format(len(candidates_entities)))
    return candidates_entities


def main():

    try:
        set_logger("st")
        log.info("==Source Tagging==")

        if os.getenv("SKIP_SI"):
            log.info("Skipping step as SKIP_SI is set")
            return
        elif os.getenv("SKIP_ST"):
            log.info("Skipping step as SKIP_ST is set")
            return

        log.info("Flag source candidates")
        flag_source_candidates()

        log.info("Flag trusted candidates")
        flag_trusted_candidates()

        log.info("Enrich sources")
        enrich_sources()

        log.info("Normal end of processing - completed")
    finally:
        # Flush logs to make sure we've got them all before leaving
        flush_logs()


# Run if local (Docker)
if __name__ == "__main__":
    main()
