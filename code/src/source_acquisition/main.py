# -*- coding: utf-8 -*-
from sqlalchemy.schema import CreateSchema
import twitter
import os
from mana_common.shared import log, flush_logs, set_logger, get_base_url, get_full_url, \
    find_rss_feed, clean_rss_path, check_if_url_match_pattern, retryable_twitter_api
from mana_common.orm import EntityStatus, Group, Entity, TwitterOrigin, WebOrigin, \
    RssOrigin, ContentType, Content, OriginGroup, ContentOrigins, TwitterProfile, Base, \
    get_session, get_db_engine, SCHEMA, get_config
from mana_common import orm

# ----------------------------------------------------------------------------------------
# /!\ Important: do NOT log (=print) outside of Python functions, this will lead to action
#                failure when deployed on IBM Cloud (using Cloud Functions)
# ----------------------------------------------------------------------------------------


# +-------------+
# | Global vars |
# +-------------+


consumer_key = None
consumer_secret = None
access_token_key = None
access_token_secret = None
db_connection_string = None

FUZZY_MATCH_SUGGESTED_THRESHOLD = 82

init_data = {
    "reference_ongs":
        [
            {
                "group": "GP",
                "entities": [
                    {"name": "greenpeacekorea", "is_reference": True, "twitter": "greenpeacekorea",
                     "web": "https://www.greenpeace.org/korea/"},
                    {"name": "GreenpeaceAP", "is_reference": True, "twitter": "GreenpeaceAP",
                     "web": "https://www.greenpeace.org.au/burntcountry"},
                    {"name": "GreenpeaceArg", "is_reference": True, "twitter": "GreenpeaceArg",
                     "web": "https://www.greenpeace.org/argentina/"},
                    {"name": "greenpeaceru", "is_reference": True, "twitter": "greenpeaceru",
                     "web": "https://greenpeace.ru/"},
                    {"name": "greenpeaceindia", "is_reference": True, "twitter": "greenpeaceindia",
                     "web": "https://www.greenpeace.org/india/en/"},
                    {"name": "milieudefensie", "is_reference": True, "twitter": "milieudefensie",
                     "web": "https://milieudefensie.nl/"},
                    {"name": "greenpeacesea", "is_reference": True, "twitter": "greenpeacesea",
                     "web": "https://www.greenpeace.org/southeastasia/"},
                    {"name": "gp_kenyagroup", "is_reference": True, "twitter": "gp_kenyagroup",
                     "web": "https://www.greenpeace.org/africa/en/"},
                    {"name": "GreenpeaceRJ", "is_reference": True, "twitter": "GreenpeaceRJ",
                     "web": "greenpeacerj.blogspot.com"},
                    {"name": "GreenpeaceAfrik", "is_reference": True, "twitter": "GreenpeaceAfrik",
                     "web": "https://www.greenpeace.org/africa/fr/"}
                ]
            },
            {
                "group": "FOE",
                "entities": [
                    {"name": "foeeurope", "is_reference": True, "twitter": "foeeurope", "web": "http://foeeurope.org/"},
                    {"name": "groundWorkSA", "is_reference": True, "twitter": "groundWorkSA",
                     "web": "http://groundwork.org.za/"},
                    {"name": "amisdelaterre", "is_reference": True, "twitter": "amisdelaterre",
                     "web": "https://www.amisdelaterre.org/"},
                    {"name": "SahabatAlamMsia", "is_reference": True, "twitter": "SahabatAlamMsia",
                     "web": "https://www.foe-malaysia.org/"},
                    {"name": "OtrosMundosChia", "is_reference": True, "twitter": "OtrosMundosChia",
                     "web": "https://otrosmundoschiapas.org/"},
                    {"name": "walhinasional", "is_reference": True, "twitter": "walhinasional",
                     "web": "https://www.walhi.or.id/"},
                    {"name": "bund_net", "is_reference": True, "twitter": "bund_net", "web": "https://www.bund.net/"},
                    {"name": "FoE_Canada", "is_reference": True, "twitter": "FoE_Canada",
                     "web": "https://foecanada.org/"},
                    {"name": "adttogo", "is_reference": True, "twitter": "adttogo",
                     "web": "https://adttogo.wordpress.com/"},
                    {"name": "Naturvern", "is_reference": True, "twitter": "Naturvern",
                     "web": "https://naturvernforbundet.no/"}
                ]
            },
            {
                "group": "WWF",
                "entities": [
                    {"name": "wwfchile", "is_reference": True, "twitter": "wwfchile", "web": "https://www.wwf.cl/"},
                    {"name": "WWFThailand", "is_reference": True, "twitter": "WWFThailand",
                     "web": "https://www.wwf.or.th/"},
                    {"name": "wwfzambia", "is_reference": True, "twitter": "wwfzambia",
                     "web": "https://www.wwfzm.panda.org/"},
                    {"name": "WWFMongolia", "is_reference": True, "twitter": "WWFMongolia",
                     "web": "https://mongolia.panda.org/"},
                    {"name": "WWF_Kenya", "is_reference": True, "twitter": "WWF_Kenya",
                     "web": "https://www.wwfkenya.org/"},
                    {"name": "WWF_Guyane", "is_reference": True, "twitter": "WWF_Guyane"},
                    {"name": "wwfRU", "is_reference": True, "twitter": "wwfRU", "web": "https://wwf.ru/"},
                    {"name": "WWF_MAR", "is_reference": True, "twitter": "WWF_MAR", "web": "https://www.wwfca.org/"},
                    {"name": "wwf_media", "is_reference": True, "twitter": "wwf_media",
                     "web": "https://wwf.panda.org/wwf_news/"},
                    {"name": "WWFnews", "is_reference": True, "twitter": "WWFnews",
                     "web": "https://www.worldwildlife.org/about/news-press"}
                ]
            }
        ],
    "entities": [
        {"name": "ECO-BUSINESS", "is_reference": False, "twitter": "ecobusinesscom",
         "web": "https://www.eco-business.com/"},
        {"name": "BANK TRACK", "is_reference": False, "twitter": "BankTrack"},
        {"name": "RAN", "is_reference": False, "twitter": "ran", "web": "https://www.ran.org/"},
        {"name": "CHAIN REACTION", "is_reference": False, "twitter": "crresearch",
         "web": "https://chainreactionresearch.com/"},
        {"name": "RAINFOREST RESCUE", "is_reference": False, "twitter": "RainforestResq"},
        {"name": "SUM OF US", "is_reference": False, "twitter": "SumOfUs"},
        {"name": "EPN", "is_reference": False, "twitter": "WhatsNYourPapr", "web": "https://environmentalpaper.org/"},
        {"name": "ANV - COP 21", "is_reference": False, "twitter": "anvcop21", "web": "https://anv-cop21.org/"},
        {"name": "MONGABAY", "is_reference": False, "twitter": "mongabay", "web": "https://news.mongabay.com"},
        {"name": "NOVETHIC", "is_reference": False, "twitter": "novethic", "web": "https://www.novethic.fr/"},
        {"name": "FOREST FINANCE", "is_reference": False, "twitter": "Forests_Finance",
         "web": "https://forestsandfinance.org/"},
        {"name": "IEA", "is_reference": False, "twitter": "IEA"},
        {"name": "MichaelEMann", "is_reference": False, "twitter": "MichaelEMann"}
    ]
}


# +-----------+
# | Functions |
# +-----------+


def add_rss_origin(web_origin):
    log.info("Adding rss origin for base_url: {}".format(web_origin.base_url))
    rss_origin = orm.get_session().query(RssOrigin).filter(
        RssOrigin.base_url == web_origin.base_url
    ).first()

    if rss_origin is None:
        log.info("New rss origin to search for: {}".format(web_origin.base_url))
        rss = find_rss_feed(web_origin.base_url)
        if rss is not None:
            rss_origin = RssOrigin(rss=rss, base_url=web_origin.base_url,
                                   origin_web=web_origin)  # TODO add web origin link
            rss_origin.rss = clean_rss_path(rss, web_origin.base_url)
            rss_origin.entity = web_origin.entity
            rss_origin.occurrences = 1
            log.info("Adding new rss origin")
            orm.get_session().add(rss_origin)
        else:
            return
    else:
        log.info("New occurrence of rss origin found for: {}".format(web_origin.base_url))
        if rss_origin.occurrences is None:
            rss_origin.occurrences = 1
        else:
            rss_origin.occurrences += 1


# Add a web origin
def add_web_origin(source_content, url):
    short_url = url.url
    expanded_url = url.expanded_url
    full_url = get_full_url(expanded_url, orm.get_config()["url_extensions_to_check_for_true_url"])

    if check_if_url_match_pattern(full_url, orm.get_config()["url_patterns_to_ignore"]):
        return None

    base_url = get_base_url(full_url, orm.get_config()["domains_where_next_element_matters"])
    web_origin = orm.get_session().query(WebOrigin).filter(
        WebOrigin.base_url == base_url
    ).first()

    if web_origin is None:
        log.info("New web origin found for: {}".format(base_url))
        web_origin = WebOrigin(raw_url=short_url, expanded_url=full_url, base_url=base_url)
        new_entity = Entity(name=base_url, is_reference=False)
        web_origin.entity = new_entity
        web_origin.occurrences = 1
        log.info("Saving new web origin")
        orm.get_session().add(web_origin)
    else:
        log.info("New occurrence of web origin found for: {}".format(base_url))
        if web_origin.occurrences is None:
            web_origin.occurrences = 1
        else:
            web_origin.occurrences += 1

    save_origin_content_relation(origin=web_origin, content=source_content)

    if not check_if_url_match_pattern(full_url, orm.get_config()["ignore_for_rss_search"]):
        add_rss_origin(web_origin)

    return web_origin


# Add a Twitter origin
def add_twitter_origin(source_content, user):
    twitter_origin = orm.get_session().query(TwitterOrigin).filter(
        TwitterOrigin.screen_name == user.screen_name
    ).first()

    if twitter_origin is None:
        # New twitter origin found
        log.info("New Twitter origin found for: {}".format(user.screen_name))
        twitter_origin = TwitterOrigin(screen_name=user.screen_name)
        new_entity = Entity(name=user.screen_name, is_reference=False)
        twitter_origin.entity = new_entity
        twitter_origin.occurrences = 1
        retrieve_and_associate_twitter_profile(twitter_origin)
        orm.get_session().add(twitter_origin)
    else:
        # New occurrence of existing origin
        log.info("New occurrence of Twitter origin found for: {}".format(user.screen_name))
        if twitter_origin.occurrences is None:
            twitter_origin.occurrences = 1
        else:
            twitter_origin.occurrences += 1

    save_origin_content_relation(origin=twitter_origin, content=source_content)

    return twitter_origin


def retrieve_and_associate_twitter_profile(twitter_origin):
    log.info("Saving tweeter profile : {}".format(twitter_origin.screen_name))
    profile = TwitterProfile()
    try:
        user_profile = retryable_twitter_api(
            function_name="GetUser",
            screen_name=twitter_origin.screen_name,
            return_json=True
        )
        profile.url = get_full_url(user_profile['url'], get_config()["url_extensions_to_check_for_true_url"])
        profile.description = user_profile['description']
        twitter_origin.twitter_profile = profile
        twitter_origin.location = user_profile['location']
    except twitter.error.TwitterError as twitter_ex:
        log.info("Error while retrieving Twitter profile: {}".format(twitter_ex))
        profile.error = str(twitter_ex)

    orm.get_session().add(profile)


# Save the tweet content and where it came from (origin)
def save_tweet_content(tweet_content, reference_origin):
    log.info("Saving tweet content produced by : {}".format(reference_origin.entity.name))
    content = Content(value=tweet_content, origin=reference_origin, content_type=ContentType.tweet)
    orm.get_session().add(content)
    return content


# Saving the relation between an origin and where it came from (content)
def save_origin_content_relation(origin, content):
    log.info("Saving relation between : {} and {}".format(
        origin.entity.name if origin.entity is not None else "No entity",
        content.value
    ))
    content_origin = ContentOrigins(content=content, origin=origin)
    orm.get_session().add(content_origin)


# We want to know by which ONG group the origin was found, in order to compute credibility rule down the road
def save_origin_group_relation(origin, entity, content):
    log.info("Saving relation with group : {} and entity : {} (content_id : {})".format(
        entity.group.name,
        entity.name,
        content.id
    ))
    origin_group = OriginGroup(origin=origin, group_id=entity.group.id, entity_id=entity.id, content_id=content.id)
    orm.get_session().add(origin_group)


# Process a list of tweets
def process_tweets(reference_origin, tweets):
    for t in tweets:
        if t.in_reply_to_status_id is None:
            content = save_tweet_content(tweet_content=t.full_text, reference_origin=reference_origin)

            for url in t.urls:
                # Web site referenced
                origin = add_web_origin(content, url)
                if origin is not None:
                    save_origin_group_relation(origin, reference_origin.entity, content)

            for user in t.user_mentions:
                origin = add_twitter_origin(content, user)
                if origin is not None:
                    save_origin_group_relation(origin, reference_origin.entity, content)


# Process a single reference entity (only tweets)
def process_reference(reference):
    # For reference entities, we parse only tweets
    for twitter_origin in reference.origins:
        if twitter_origin.type == 'twitter_origin':
            process_twitter_reference(twitter_origin)


# Process a Twitter origin for a reference entity
def process_twitter_reference(twitter_origin):
    log.info("Processing screen name: {}".format(twitter_origin.screen_name))

    try:
        # get last tweets
        tweets = retryable_twitter_api(
            function_name="GetUserTimeline",
            screen_name=twitter_origin.screen_name,
            since_id=twitter_origin.last_synced_id,
            count=orm.get_config()["max_tweets"]
        )
    except twitter.error.TwitterError as twitter_ex:
        log.error("Error while retrieving the latest tweets from the Twitter account: {}".format(twitter_ex))
        # indicate that the account triggered an error - to be dealt with manually in the database
        twitter_origin.valid_extraction = False
        return

    log.info('{} has {} new tweet(s)'.format(twitter_origin.screen_name, len(tweets)))

    process_tweets(twitter_origin, tweets)

    # Find highest ID for next sync
    max_id = -1
    for t in tweets:
        if t.id > max_id:
            max_id = t.id
    if max_id > 0:
        twitter_origin.last_synced_id = max_id
        orm.get_session().commit()


# Process a list of reference entities
def process_references():
    processed_references = []

    query = orm.get_session().query(Entity).filter(Entity.is_reference)
    reference_entities = query.all()
    length = len(reference_entities)
    log.info("Contributors will be searched from {} entity(ies)".format(length))
    count = 1

    try:
        for r in reference_entities:
            processed_references.append(process_reference(r))
            log.info("Processed {} / {}".format(count, length))
            count += 1
    finally:
        orm.get_session().commit()


def init_entity(e, status, group):
    log.info("Entity : {}".format(e['name']))
    entity = Entity(
        name=e['name'],
        is_reference=e['is_reference'],
        status=status,
        trusted=True  # Trusted = True, Based on Capucine comment on the 20-07-01 ONG INT Twitter + RSS.xlsx
    )
    if group is not None:
        entity.group = group
    if 'twitter' in e:
        twitter_origin = TwitterOrigin(screen_name=e['twitter'])
        twitter_origin.entity = entity
        retrieve_and_associate_twitter_profile(twitter_origin)
    if 'web' in e:
        web_origin = WebOrigin(raw_url=e['web'], expanded_url=e['web'], base_url=e['web'], entity=entity)
        rss = find_rss_feed(web_origin.base_url)
        if rss is not None:
            rss_origin = RssOrigin(rss=rss, base_url=web_origin.base_url, origin_web=web_origin)
            rss_origin.entity = web_origin.entity
    get_session().add(entity)


# Initializes the database (clears it all)
def kickstart_db_if_needed():
    if not get_db_engine().dialect.has_schema(get_db_engine(), SCHEMA):
        log.info("Schema '{}' not found in database, will initialize".format(SCHEMA))
        get_db_engine().execute(CreateSchema(SCHEMA))

        Base.metadata.create_all(get_db_engine())

        for g in init_data['reference_ongs']:
            group = Group(name=g['group'])
            get_session().add(group)
            log.info("Created group : {0}".format(group.name))
            for e in g['entities']:
                init_entity(e=e, status=EntityStatus.SOURCE, group=group)

        for e in init_data['entities']:
            init_entity(e=e, status=EntityStatus.SOURCE, group=None)

        get_session().commit()
    else:
        log.info("Schema '{}' found in database, assuming it is properly setup".format(SCHEMA))


def main():
    try:
        set_logger("sa")
        log.info("==Source Acquisition==")
        kickstart_db_if_needed()

        if os.getenv("SKIP_SI"):
            log.info("Skipping step as SKIP_SI is set")
            return
        elif os.getenv("SKIP_SA"):
            log.info("Skipping step as SKIP_SA is set")
            return

        process_references()
        log.info("Normal end of processing - completed")
    finally:
        # Flush logs to make sure we've got them all before leaving
        flush_logs()


# Run if local (Docker)
if __name__ == "__main__":
    main()
