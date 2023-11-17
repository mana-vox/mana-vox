from mana_common.shared import log, find_rss_feed, check_if_url_match_pattern
from mana_common.orm import Group, Entity, Origin, TwitterOrigin, WebOrigin, RssOrigin, update_tweeter_profile, \
    retrieve_tweeter_profile, create_web_and_rss_origins, create_twitter_origin, get_config
from api.utils import list_duplicates
import pandas as pd
from fastapi import HTTPException

source_name_column = "Source"
group_column = "Groupe"
is_reference_column = "Reference"
twitter_column = "Twitter"
web_column = "Web"
is_trusted_column = "Trusted"
location_column = "Location"
ecoregion_column = "Ecoregion"
tags_column = "Tags"
created_column = "Created"
updated_column = "Updated"
deleted_column = "Deleted"


def check_sources_duplicates(df):
    errors = []
    sources_duplicates = list_duplicates(df, source_name_column)
    twitter_account_duplicates = list_duplicates(df, twitter_column)
    web_sites_duplicates = list_duplicates(df, web_column)

    for col in [ [ source_name_column, sources_duplicates] , [twitter_column, twitter_account_duplicates], [web_column, web_sites_duplicates]]:
        if len(col[1]) > 0:
            errors.append({ "error" : "There are some duplicates in the " + col[0] + " column", "duplicates" : col[1] })

    if (len(errors) > 0):
        raise HTTPException(status_code=500, detail= { "errors" : errors } )

def process_groups(db, groups):
    log.info("Processing {} groups".format(len(groups)))
    added_groups =[]
    for g in groups:
        # makes sure None group is not added
        if not pd.isna(g) and g is not None:
            db_group = db.query(Group).filter(Group.name == g).first()
            if db_group is None:
                log.info("Create group {}".format(g))
                db_group = Group(name=g)
                db.add(db_group)
                added_groups.append(g)
            else:
                log.info("Group {} is already in db".format(g))
    return added_groups

def create_source(db, source_name, group, is_reference, twitter, web, is_trusted, location, ecoregion, tags):
    log.info("Creating {}".format(source_name))
    entity = Entity(name=source_name, is_reference=is_reference, status="SOURCE", trusted=is_trusted, location=location, ecoregion=ecoregion) #Trusted = True, Based on Capucine comment on the 20-07-01 ONG INT Twitter + RSS.xlsx
    
    if not pd.isna(tags) and tags is not None:
        entity.tags = tags.split(",")
    
    if not pd.isna(group) and group is not None:
        db_group = db.query(Group).filter(Group.name == group).first()
        if db_group != None:
            entity.group = db_group
    
    if not pd.isna(twitter) and twitter is not None:
        log.info("A twitter account is provided {}".format(twitter))
        create_twitter_origin(entity, twitter)

    if not pd.isna(web) and web is not None:
        log.info("A web site is provided {}".format(web))
        create_web_and_rss_origins(entity, web)
        
    db.add(entity)

def update_source(db, entity, group, is_reference, twitter, web, is_trusted, location, ecoregion, tags):
    log.info("Updating {}".format(entity.name))
    # Change status if the source already existed as SOURCE_CANDIDATE
    entity.status = "SOURCE"

    db_group = db.query(Group).filter(Group.name == group).first()
    if db_group != None:
        entity.group = db_group

    # only updates if the source became a reference - otherwise stays at FALSE or TRUE (to make sure a reference is not deleted)
    if is_reference:
        entity.is_reference = is_reference

    if is_trusted:
        entity.trusted = is_trusted

    if not pd.isna(location) and location is not None:
        entity.location = location

    if not pd.isna(ecoregion) and ecoregion is not None:
        entity.ecoregion = ecoregion
    
    if not pd.isna(tags) and tags is not None:
        if entity.tags is not None:
            for tag in tags.split(","):
                if tag not in entity.tags:
                    entity.tags = entity.tags + [tag]
        else:
            entity.tags = tags.split(",")
    else:
        entity.tags = []

    if not pd.isna(twitter) and twitter is not None:
        log.info("A twitter account is provided {}".format(twitter))
        previous_twitter_origins = list(filter(lambda o : isinstance(o, TwitterOrigin), entity.origins))
        if (len(previous_twitter_origins) > 0):
            if (previous_twitter_origins[0].screen_name != twitter):
                log.info("Updating twitter origin {} to {}".format(previous_twitter_origins[0].screen_name, twitter))
                existing_twitter_origin_in_db = db.query(TwitterOrigin).filter(TwitterOrigin.screen_name == twitter).first()
                if existing_twitter_origin_in_db != None:
                    log.info("this account already exists in db {}".format(twitter))
                    previous_twitter_origins[0]=existing_twitter_origin_in_db
                else:
                    previous_twitter_origins[0].screen_name = twitter
                    if previous_twitter_origins[0].twitter_profile != None:
                        update_tweeter_profile(previous_twitter_origins[0])
                    else:
                        retrieve_tweeter_profile(previous_twitter_origins[0])
            else:
                log.info("Twitter account did not change")
        else:
            log.info("Adding a new twitter origin {}".format(twitter))
            create_twitter_origin(entity, twitter)

    if not pd.isna(web) and web is not None:
        log.info("A web site is provided {}".format(web))
        previous_web_origins = list(filter(lambda o : isinstance(o, WebOrigin), entity.origins))
        if len(previous_web_origins) > 0:
            web_origin = previous_web_origins[0]
            if (web_origin.raw_url != web):
                log.info("Updating web origin {} to {}".format(web_origin.raw_url, web))
                web_origin.raw_url = web
                web_origin.base_url = web
                web_origin.expanded_url = web
                if not check_if_url_match_pattern(web, get_config()["ignore_for_rss_search"]):
                    rss = find_rss_feed(web)
                    previous_rss_origins = list(filter(lambda o : isinstance(o, RssOrigin), entity.origins))
                    if rss is not None:
                        log.info("Found rss {}".format(rss))
                        
                        if len(previous_rss_origins) > 0:
                            log.info("Previous web site has an rss, updating it")
                            rss_origin = previous_rss_origins[0]
                            rss_origin.rss = rss
                            rss_origin.base_url = web_origin.base_url
                        else:
                            log.info("Previous web site has NO rss, adding it")
                            rss_origin = RssOrigin(rss=rss, base_url=web_origin.base_url, origin_web=web_origin)
                            rss_origin.entity = web_origin.entity
                    else:
                        log.info("New web site has NO rss, removing rss link")
                        web_origin.rss_origin = []
                else:
                    log.info("Ignoring the RSS search based on provided url and ignore_for_rss_search")
            else:
                log.info("web site did not change")

        else:
            log.info("Adding a new web origin {}".format(web))
            create_web_and_rss_origins(entity, web)

    db.add(entity)

def process_sources(db, df):
    log.info("Processing sources")
    added_groups = process_groups(db, list(df[group_column].unique()))
    log.info("Added {} groups".format(len(added_groups)))

    for index, row in df.iterrows():
        log.info("Processing source {}".format(row[source_name_column]))
        # search existing entity by name
        db_entity = db.query(Entity).filter(Entity.name == row[source_name_column]).first()
        # search existing entity by twitter account
        db_twitter = db.query(TwitterOrigin).filter(TwitterOrigin.screen_name == row[twitter_column]).first()

        if db_entity is None:
            # check if another entity has the same twitter account as it can only be linked to one entity
            if db_twitter is None:
                log.info("Source does not exist, creating it")
                create_source(db=db, source_name=row[source_name_column], group=row[group_column], is_reference=row[is_reference_column], twitter=row[twitter_column], web=row[web_column], is_trusted=row[is_trusted_column], location=row[location_column], ecoregion=row[ecoregion_column], tags=row[tags_column])
                df.at[index, created_column]=1
            else:
                log.info("Source already exists, will update")
                # retrieve source name through twitter account
                db_origin = db.query(Origin).filter(Origin.id == db_twitter.id).first()
                db_twitter_entity = db.query(Entity).filter(Entity.id == db_origin.entity_id).first()
                df.at[index, updated_column]=1
                update_source(db=db, entity=db_twitter_entity, group=row[group_column], is_reference=row[is_reference_column], twitter=row[twitter_column], web=row[web_column], is_trusted=row[is_trusted_column], location=row[location_column], ecoregion=row[ecoregion_column], tags=row[tags_column])
        else:
            log.info("Source already exists, will update")
            df.at[index, updated_column]=1
            update_source(db=db, entity=db_entity, group=row[group_column], is_reference=row[is_reference_column], twitter=row[twitter_column], web=row[web_column], is_trusted=row[is_trusted_column], location=row[location_column], ecoregion=row[ecoregion_column], tags=row[tags_column])
    return df

def compute_sources_kpis(df):
    added = list(df[df[created_column] == 1][source_name_column]) if created_column in df.columns else []
    updated = list(df[df[updated_column] == 1][source_name_column]) if updated_column in df.columns else []
    deleted = list(df[df[deleted_column] == 1][source_name_column]) if deleted_column in df.columns else []
    return { 
        "nb_added_sources" : len(added),
        "nb_updated_sources" : len(updated),
        "nb_deleted_or_suspended_sources" : len(deleted),
        "added_sources" : added,
        "updated_sources" : updated,
        "deleted_or_suspended_sources" : deleted
    }