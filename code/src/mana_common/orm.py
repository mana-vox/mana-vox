import os

from sqlalchemy.orm import relationship, backref
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import create_engine, Column, ForeignKey, Enum, \
    Integer, String, Sequence, Float, Boolean, BigInteger, ARRAY, DateTime
from sqlalchemy.orm.session import sessionmaker
from sqlalchemy.dialects.postgresql import JSON
from functools import reduce
from sqlalchemy.sql import func
import enum
from mana_common.shared import log, get_twitter_infos, find_rss_feed, find_company_name_matches
from datetime import datetime

DB_POOL_SIZE = 30
DB_MAX_OVERFLOW = 3
SCHEMA = "manav3"

_db_engine = None
_session = None
_config = None
_cache_companies = None


# ----------------------------------------------------------------------------------------
# /!\ Important: do NOT log (=print) outside of Python functions, this will lead to action
#                failure when deployed on IBM Cloud (using Cloud Functions)
# ----------------------------------------------------------------------------------------


Base = declarative_base()


def get_config():
    global _config
    if _config is not None:
        return _config

    db_config = get_session().query(Config).filter(Config.id == 1).first()
    if not db_config:
        log.info("Config is not present, creating")
        db_config = Config(id=1)
        get_session().add(db_config)
        get_session().commit()
    _config = {
        "max_tweets": db_config.max_tweets,
        "trusted_source_origins_threshold": db_config.trusted_source_origins_threshold,
        "trusted_source_group_threshold": db_config.trusted_source_group_threshold,
        "source_candidate_threshold": db_config.source_candidate_threshold,
        "use_nlu_for_company_detection": db_config.use_nlu_for_company_detection,
        "url_extensions_to_check_for_true_url": db_config.url_extensions_to_check_for_true_url,
        "domains_where_next_element_matters": db_config.domains_where_next_element_matters,
        "ignore_for_rss_search": db_config.ignore_for_rss_search,
        "url_patterns_to_ignore": db_config.url_patterns_to_ignore,
        "url_pattern_to_ignore_for_content_analysis": db_config.url_pattern_to_ignore_for_content_analysis,
        "social_keywords_pattern": db_config.social_keywords_pattern
    }

    # Override config parameters if applicable
    max_tweets = os.environ.get("MAX_TWEETS")
    if max_tweets is not None:
        log.info("Overriding max_tweets: {} -> {}".format(_config["max_tweets"], int(max_tweets)))
        _config["max_tweets"] = int(max_tweets)

    trusted_source_origins_threshold = os.environ.get("TRUSTED_SOURCE_ORIGINS_THRESHOLD")
    if trusted_source_origins_threshold is not None:
        log.info("Overriding trusted_source_origins_threshold: {} -> {}".format(
            _config["trusted_source_origins_threshold"], int(trusted_source_origins_threshold)
        ))
        _config["trusted_source_origins_threshold"] = int(trusted_source_origins_threshold)

    trusted_source_group_threshold = os.environ.get("TRUSTED_SOURCE_GROUPS_THRESHOLD")
    if trusted_source_group_threshold is not None:
        log.info("Overriding trusted_source_group_threshold: {} -> {}".format(
            _config["trusted_source_group_threshold"], int(trusted_source_group_threshold)
        ))
        _config["trusted_source_group_threshold"] = int(trusted_source_group_threshold)

    source_candidate_threshold = os.environ.get("SOURCE_CANDIDATE_THRESHOLD")
    if source_candidate_threshold is not None:
        log.info("Overriding source_candidate_threshold: {} -> {}".format(
            _config["source_candidate_threshold"], int(source_candidate_threshold)
        ))
        _config["source_candidate_threshold"] = int(source_candidate_threshold)

    use_nlu_for_company_detection = os.environ.get("USE_NLU_FOR_COMPANY_DETECTION")
    if use_nlu_for_company_detection is not None:
        log.info("Overriding use_nlu_for_company_detection: {} -> {}".format(
            _config["use_nlu_for_company_detection"], bool(use_nlu_for_company_detection)
        ))
        _config["use_nlu_for_company_detection"] = bool(use_nlu_for_company_detection)

    return _config


def setup_db(db_connection_string):
    global _db_engine, _session
    _db_engine = create_engine(db_connection_string, pool_size=DB_POOL_SIZE, max_overflow=DB_MAX_OVERFLOW)
    _session = sessionmaker(bind=_db_engine)()
    log.info("DB engine & session set")


def get_session():
    if _session is None:
        log.info("DB session is not set - setting up")
        setup_db(os.environ.get("DB_CONNECTION_STRING"))
    return _session


def get_db_engine():
    if _db_engine is None:
        log.info("DB engine is not set - setting up")
        setup_db(os.environ.get("DB_CONNECTION_STRING"))
    return _db_engine


def get_companies_cache():
    if _cache_companies is None:
        log.info("Attempting to use cached companies while content was not fetched, will fetch now")
        load_cache_companies()
    return _cache_companies


def sum_entity_origin_occurrences(distinct_origins, entity_name):
    log.info("Entity : {0}".format(entity_name))
    sum_occurrences = reduce(lambda occ, val: occ + val.occurrences, distinct_origins, 0)

    refs = []
    for o in distinct_origins:
        s = "id={}, occ={}".format(o.id, o.occurrences)
        log.info("References: {}".format(s))
        refs.append("id={}, occ={}".format(o.id, o.occurrences))
    log.info("Sum occurrences : {}, refs : {}".format(sum_occurrences, refs))
    return sum_occurrences, refs


# For RSS and Web : we don't want to double count origins. We will only count RSS that don't have a related web origin (most likely added manually)
def list_distinct_origins(entity):
    rss_origins = [o for o in entity.origins if o.type == 'rss_origin']
    rss_origins_no_web_origin = [o for o in rss_origins if o.origin_web is None]
    twitter_origins = [o for o in entity.origins if o.type == 'twitter_origin']
    web_origins = [o for o in entity.origins if o.type == 'web_origin']
    return rss_origins_no_web_origin + web_origins + twitter_origins


def flag_source_candidate(entity):
    source_candidate_threshold = get_config()["source_candidate_threshold"]
    # Source candidates are entities that :
    # - have a status ENTITY (= not yet SOURCE_CANDIDATE, nor SOURCE, nor TRUSTED_SOURCE)
    # - have a total origin occurrences > threshold
    distinct_origins = list_distinct_origins(entity)
    occ, refs = sum_entity_origin_occurrences(distinct_origins, entity.name)
    if occ >= source_candidate_threshold:
        log.info("{0} has enough occurrences {1} to be flagged as source candidate".format(entity.name, occ))
        entity.status = EntityStatus.SOURCE_CANDIDATE


# For a candidate entity to become TRUSTED, sum(occurences(origins)) >= 10 across at least two ONG groups
def compute_distinct_groups_origins_came_from(distinct_origins, entity_name):
    log.info("Entity : {0}".format(entity_name))
    groups = []
    for origin in distinct_origins:
        for group in origin.mentioned_by_groups:
            groups.append(group.group_id)
    distinct_groups = set(groups)
    log.info("Distinct groups : {0}".format(distinct_groups))
    return len(distinct_groups)


def flag_trusted_candidate(entity):
    trusted_source_group_threshold = get_config()["trusted_source_group_threshold"]
    trusted_source_origins_threshold = get_config()["trusted_source_origins_threshold"]

    distinct_origins = list_distinct_origins(entity)
    occ, refs = sum_entity_origin_occurrences(distinct_origins, entity.name)
    if occ >= trusted_source_origins_threshold:
        log.info("{0} has enough occurrences for checking distinct groups {1}".format(entity.name, occ))
        distinct_groups = compute_distinct_groups_origins_came_from(distinct_origins, entity.name)
        if distinct_groups >= trusted_source_group_threshold:
            log.info("{0} was referenced by enough groups {1}".format(entity.name, distinct_groups))
            entity.trusted = True


def enrich_source(entity):
    references = {}
    distinct_origins = list_distinct_origins(entity)
    occ, ref = sum_entity_origin_occurrences(distinct_origins, entity.name)
    entity.t_occurrences = occ
    log.info("There is(are) {} distinct origins for entity '{}' (id={})".format(len(distinct_origins), entity.name, entity.id))
    for origin in distinct_origins:
        ref_query = get_session().query(OriginGroup).filter(OriginGroup.origin_id == origin.id)
        origin_groups = ref_query.all()
        for origin_group in origin_groups:
            if origin_group.entity.name not in references:
                references[origin_group.entity.name] = {"number": 1, "content": [origin_group.content.id], "group": origin_group.group.name}
            else:
                references[origin_group.entity.name]["number"] += 1
                references[origin_group.entity.name]["content"].append(origin_group.content.id)

    entity.t_source_references = references


# e2 will be merged into e1; e2 will we removed (unless e2 is a reference)
def merge_entities(e1_id, e2_id, reason):
    log.info("Request to merge entities {} and {}".format(e1_id, e2_id))

    entity1 = get_session().query(Entity).get(e1_id)
    entity2 = get_session().query(Entity).get(e2_id)

    if entity1 is None:
        message = "Cannot merge entities {} and {} as entity {} does not exist".format(e1_id, e2_id, e1_id)
        log.info(message)
        return False, message

    if entity2 is None:
        message = "Cannot merge entities {} and {} as entity {} does not exist".format(e1_id, e2_id, e2_id)
        log.info(message)
        return False, message

    if e1_id == e2_id:
        message = "Cannot merge identical entities"
        log.info(message)
        return False, message

    if entity1.is_reference and entity2.is_reference:
        message = "Cannot merge entities {} and {} as they are both references".format(e1_id, e2_id)
        log.info(message)
        return False, message

    if entity1.group_id is not None and entity2.group_id is not None and entity1.group_id != entity2.group_id:
        message = "Cannot merge entities {} and {} as they belong to different groups".format(e1_id, e2_id)
        log.info(message)
        return False, message

    # Make sure we keep the reference if we have one
    entity_to_keep, entity_to_wipe = (entity1, entity2) if not entity2.is_reference else (entity2, entity1)

    log.info("Will merge {} into {}".format(entity_to_wipe.id, entity_to_keep.id))

    # Verification state: "min"
    verification_state = min(entity_to_keep.verification_state.value, entity_to_wipe.verification_state.value)
    entity_to_keep.verification_state = EntityVerificationState(verification_state)
    log.info("Merge verification state: {}".format(entity_to_keep.verification_state))

    # Entity status: "max"
    entity_status = max(entity_to_keep.status.value, entity_to_wipe.status.value)
    entity_to_keep.status = EntityStatus(entity_status)
    log.info("Merge status: {}".format(entity_to_keep.status))

    # Trusted: "and"
    entity_to_keep.trusted = entity_to_keep.trusted and entity_to_wipe.trusted
    log.info("Merge trusted: {}".format(entity_to_keep.trusted))

    # Merges: "add"
    entity_to_keep.merges += entity_to_wipe.merges + 1
    log.info("Merge merges: {}".format(entity_to_keep.merges))

    # Match done: "and"
    entity_to_keep.match_done = entity_to_keep.match_done and entity_to_wipe.match_done
    log.info("Merge match_done: {}".format(entity_to_keep.match_done))

    # Merge suggestions: "merge json"
    etk_suggested_merges = [] if entity_to_keep.suggested_merges is None else entity_to_keep.suggested_merges.copy()
    etw_suggested_merges = [] if entity_to_wipe.suggested_merges is None else entity_to_wipe.suggested_merges.copy()
    etk_suggested_merges.extend(etw_suggested_merges)
    # Remove references to entities that are being merged
    etk_suggested_merges = list(
        filter(
            lambda x: x["id"] != entity_to_wipe.id and x["id"] != entity_to_keep.id,
            etk_suggested_merges
        )
    )
    # Remove duplicates
    entity_to_keep.suggested_merges = []
    id_added = []
    for i in etk_suggested_merges:
        if not i["id"] in id_added:
            id_added.append(i["id"])
            entity_to_keep.suggested_merges.append(i)
    if len(entity_to_keep.suggested_merges) == 0:
        entity_to_keep.suggested_merges = None
    log.info("Merge suggested_merges: {}".format(entity_to_keep.suggested_merges))

    # Comments: "add"
    etk_comments = [] if entity_to_keep.comments is None else entity_to_keep.comments.copy()
    etw_comments = [] if entity_to_wipe.comments is None else entity_to_wipe.comments.copy()
    etk_comments.extend(etw_comments)
    if len(etk_comments) > 0:
        entity_to_keep.comments = etk_comments
    log.info("Merge comments: {}".format(entity_to_keep.comments))

    # Origins: "add"
    for o in entity_to_wipe.origins:
        o.entity = entity_to_keep

    # Merge details: "add"
    etk_details = [] if entity_to_keep.merges_details is None else entity_to_keep.merges_details.copy()
    etw_details = [] if entity_to_wipe.merges_details is None else entity_to_wipe.merges_details.copy()
    etk_details.extend(etw_details)
    etk_details.append('Merged with (removed) entity {} ({})'.format(entity_to_wipe.id, reason))
    entity_to_keep.merges_details = list(etk_details)
    log.info("Merge merges_details: {}".format(entity_to_keep.merges_details))

    # Group id (note: cannot be different)
    if entity_to_wipe.group_id is not None and entity_to_keep.group_id is None:
        entity_to_keep.group_id = entity_to_wipe.group_id
    log.info("Merge group_id: {}".format(entity_to_keep.group_id))

    # Match done: "or"
    entity_to_keep.match_done = entity_to_keep.match_done or entity_to_wipe.match_done
    log.info("Merge match_done: {}".format(entity_to_keep.match_done))

    # Update enrichment as well
    if entity_to_keep.match_done:
        flag_source_candidate(entity_to_keep)
        flag_trusted_candidate(entity_to_keep)
        if entity_to_keep.status == EntityStatus.SOURCE_CANDIDATE:
            enrich_source(entity_to_keep)

    # Finally delete unwanted
    log.info("Deleting entity {}".format(entity_to_wipe.id))
    get_session().delete(entity_to_wipe)

    log.info("Merge is complete")

    return True, "Entity id={} was merged into entity id={}".format(entity_to_wipe.id, entity_to_keep.id)


def create_twitter_origin(entity, twitter_screen_name):
    log.info("create twitter origin for screen_name {}".format(twitter_screen_name))
    twitter_origin = TwitterOrigin(screen_name=twitter_screen_name)
    twitter_origin.entity = entity
    retrieve_tweeter_profile(twitter_origin)

def create_web_and_rss_origins(entity, web):
    log.info("create web and rss origin for url {}".format(web))
    web_origin = WebOrigin(raw_url=web, expanded_url=web, base_url=web, entity=entity)
    rss = find_rss_feed(web_origin.base_url)
    if rss is not None:
        log.info("Found rss {}".format(rss))
        rss_origin = RssOrigin(rss=rss, base_url=web_origin.base_url, origin_web=web_origin)
        rss_origin.entity = web_origin.entity

def update_tweeter_profile(twitter_origin):
    log.info("Updating tweeter profile : {}".format(twitter_origin.screen_name))
    url, description, location = get_twitter_infos(twitter_origin.screen_name, get_config()['url_extensions_to_check_for_true_url'])
    if url != None:
        twitter_origin.twitter_profile.url = url
    if description != None:
        twitter_origin.twitter_profile.description = description
    if location != None:
        twitter_origin.location = location

def retrieve_tweeter_profile(twitter_origin):
    log.info("Saving tweeter profile : {}".format(twitter_origin.screen_name))
    url, description, location = get_twitter_infos(twitter_origin.screen_name, get_config()['url_extensions_to_check_for_true_url'])
    # if user account doesn't exist, values are set to None
    if url != None: # if url is set, account exists and has a description
        profile = TwitterProfile(url=url, description=description)
        twitter_origin.twitter_profile = profile
    if location != None:
        twitter_origin.location = location

# Load companies in pure JSON to avoid db interaction and therefore make much faster queries
def load_cache_companies():
    log.info("loading companies from db")
    global _cache_companies
    _cache_companies = []
    sql_companies = get_session().query(Company).all()
    for sql_company in sql_companies:
        _cache_companies.append({"name": sql_company.name, "synonyms": [s.name for s in sql_company.synonyms]})
    log.info('Found {0} companies'.format(len(_cache_companies)))

def find_companies(text):
    # Let's try to find a company in this content
    log.info("Trying to find matching company in content")
    matching_companies = []
    for company in get_companies_cache():
        matched = {"company": None, "synonyms": [], "match_details": []}
        match = find_company_name_matches(text, company["name"])
        if match is not None:
            matched["company"] = company["name"]
            matched["match_details"] = match
        for syn in company["synonyms"]:
            match = find_company_name_matches(text, syn)
            if match is not None:
                matched["company"] = company["name"]
                matched["synonyms"].append(syn)
                matched["match_details"] = matched["match_details"] + match
        if matched["company"] is not None:
            matching_companies.append(matched)
    log.info("Found {}".format(len(matching_companies)))
    return matching_companies


def delete_previous_analysis(content):
    if get_session() is not None:  # so that the analysis function can be ran autonomously from the main()
        try:
            previous_analysis = get_session().query(Analysis).filter(
                Analysis.content_id == content.id
            ).all()
        except Exception as ex:
            log.info(
                "[delete_previous_analysis] Unable to query, most likely due to a db schema update (you may need to recreate analysis table), exiting ({})".format(ex))
            exit()
            return

        for analysis in previous_analysis:
            log.info(
                "[delete_previous_analysis] deleting a previous analysis")
            get_session().delete(analysis)

def initialize_analysis(content):
    analysis_ts = int(datetime.now().timestamp())
    analysis = Analysis(
        flag=-1,
        nlu_company_confidence=0,
        weighted_score_company=0,
        score=0,
        count=0,
        score_keywords_confirmed=0,
        content=content,
        analysis_ts=analysis_ts
    )

    return analysis

# For now we don't need inheritance and polymorphism for the content table (no specific attributes ?)
class ContentType(enum.Enum):
    tweet = 1
    web = 2
    rss = 3


class EntityStatus(enum.Enum):
    ENTITY = 1
    SOURCE_CANDIDATE = 2
    SOURCE = 3


class AnalysisStatus(enum.Enum):
    failed_at_translation = 1
    no_companies = 2
    other_exception = 3
    completed = 4
    no_content = 5

class AnalysisType(enum.Enum):
    tweet = 1
    html = 2
    pdf = 3

class AnalysisExpertManaStatus(enum.Enum):
    OUI_MANA = 1,
    NON_MANA = 2,
    A_REVOIR = 3

class EntityVerificationState(enum.Enum):
    NOT_VERIFIED = 1
    PARTIALLY_VERIFIED = 2
    VERIFIED = 3


class Config(Base):
    __tablename__ = 'config'
    __table_args__ = {'schema': SCHEMA}
    id = Column(Integer, primary_key=True)
    max_tweets = Column(Integer, default=20)
    trusted_source_origins_threshold = Column(Integer, default=10)
    trusted_source_group_threshold = Column(Integer, default=2)
    source_candidate_threshold = Column(Integer, default=3)
    use_nlu_for_company_detection = Column(Boolean, default=False)
    url_extensions_to_check_for_true_url = Column(ARRAY(String), default=[".ly/", ".co/", "lnkd.in/"])
    domains_where_next_element_matters = Column(ARRAY(String), default=["facebook.com", "twitter.com"])
    ignore_for_rss_search = Column(ARRAY(String), default=["facebook.com", "twitter.com"])
    url_patterns_to_ignore = Column(ARRAY(String), default=[r"(facebook\.com\/events\/)[0-9]*"])
    url_pattern_to_ignore_for_content_analysis = Column(ARRAY(String), default=['twitter.com', 'youtube.com', 'facebook.com', '.pdf'])
    social_keywords_pattern = Column((String), default='(facebook|twitter|linkedin|instagram|youtube)')
    time_created = Column(DateTime(timezone=True), server_default=func.now())
    time_updated = Column(DateTime(timezone=True), onupdate=func.now())


class Group(Base):
    __tablename__ = 'groups'
    __table_args__ = {'schema': SCHEMA}
    id = Column(Integer, Sequence('groups_id_seq', schema=SCHEMA), primary_key=True)
    name = Column(String)
    time_created = Column(DateTime(timezone=True), server_default=func.now())
    time_updated = Column(DateTime(timezone=True), onupdate=func.now())


class Entity(Base):
    __tablename__ = 'entities'
    __table_args__ = {'schema': SCHEMA}
    id = Column(Integer, Sequence('entities_id_seq', schema=SCHEMA), primary_key=True)
    name = Column(String)
    is_reference = Column(Boolean)
    verification_state = Column(Enum(EntityVerificationState), default=EntityVerificationState.NOT_VERIFIED)
    status = Column(Enum(EntityStatus), default=EntityStatus.ENTITY)
    trusted = Column(Boolean, default=False)
    origins = relationship("Origin", backref="entity")
    merges = Column(Integer, default=0)
    suggested_merges = Column(ARRAY(JSON))
    merges_details = Column(ARRAY(JSON))
    group_id = Column(Integer, ForeignKey(SCHEMA + '.groups.id'))
    group = relationship(Group, backref="entities", foreign_keys=[group_id])
    t_occurrences = Column(Integer)
    t_source_references = Column(JSON)
    comments = Column(ARRAY(String))
    match_done = Column(Boolean, default=False)
    location = Column(String)
    ecoregion = Column(String)
    tags = Column(ARRAY(String))
    time_created = Column(DateTime(timezone=True), server_default=func.now())
    time_updated = Column(DateTime(timezone=True), onupdate=func.now())


class Origin(Base):
    __tablename__ = 'origins'
    __table_args__ = {'schema': SCHEMA}
    id = Column(Integer, Sequence('origins_id_seq', schema=SCHEMA), primary_key=True)
    type = Column(String)
    occurrences = Column(Integer, default=0)
    entity_id = Column(Integer, ForeignKey(SCHEMA + '.entities.id'))
    contents = relationship("Content", backref="origin")
    last_synced_id = Column(BigInteger)
    time_created = Column(DateTime(timezone=True), server_default=func.now())
    time_updated = Column(DateTime(timezone=True), onupdate=func.now())

    __mapper_args__ = {
        'polymorphic_identity': 'origin',
        'polymorphic_on': type
    }


class TwitterProfile(Base):
    __tablename__ = 'twitter_profiles'
    __table_args__ = {'schema': SCHEMA}
    id = Column(Integer, Sequence('twitter_profiles_id_seq', schema=SCHEMA), primary_key=True)
    description = Column(String)
    url = Column(String)
    error = Column(String)
    time_created = Column(DateTime(timezone=True), server_default=func.now())
    time_updated = Column(DateTime(timezone=True), onupdate=func.now())


class TwitterOrigin(Origin):
    __tablename__ = 'origins_twitter'
    __table_args__ = {'schema': SCHEMA}
    id = Column(Integer, ForeignKey(SCHEMA + '.origins.id'), primary_key=True)
    screen_name = Column(String, unique=True)
    location = Column(String)
    valid_extraction = Column(Boolean, default=True)
    twitter_profile_id = Column(Integer, ForeignKey(SCHEMA + '.twitter_profiles.id'))
    twitter_profile = relationship(TwitterProfile, backref="twitter_origin")

    __mapper_args__ = {
        'polymorphic_identity': 'twitter_origin',
    }


class WebOrigin(Origin):
    __tablename__ = 'origins_web'
    __table_args__ = {'schema': SCHEMA}
    id = Column(Integer, ForeignKey(SCHEMA + '.origins.id'), primary_key=True)
    raw_url = Column(String)
    expanded_url = Column(String)
    base_url = Column(String)
    valid_extraction = Column(Boolean, default=True)

    __mapper_args__ = {
        'polymorphic_identity': 'web_origin',
    }


class RssOrigin(Origin):
    __tablename__ = 'origins_rss'
    __table_args__ = {'schema': SCHEMA}
    id = Column(Integer, ForeignKey(SCHEMA + '.origins.id'), primary_key=True)
    base_url = Column(String)
    rss = Column(String)
    valid_extraction = Column(Boolean, default=True)

    origins_web_id = Column(Integer, ForeignKey(SCHEMA + '.origins_web.id'))
    origin_web = relationship(WebOrigin, backref="rss_origin", foreign_keys=[origins_web_id])

    __mapper_args__ = {
        'polymorphic_identity': 'rss_origin',
    }


class Content(Base):
    __tablename__ = 'contents'
    __table_args__ = {'schema': SCHEMA}
    id = Column(Integer, Sequence('content_id_seq', schema=SCHEMA), primary_key=True)
    content_type = Column(Enum(ContentType))
    value = Column(String)
    link = Column(String)
    analysis_ts = Column(BigInteger)
    origin_id = Column(Integer, ForeignKey(SCHEMA + '.origins.id'))
    time_created = Column(DateTime(timezone=True), server_default=func.now())
    time_updated = Column(DateTime(timezone=True), onupdate=func.now())


class OriginGroup(Base):
    __tablename__ = 'origins_mentioned_by_groups'
    __table_args__ = {'schema': SCHEMA}
    id = Column(Integer, Sequence('origins_mentioned_by_groups_id_seq', schema=SCHEMA), primary_key=True)
    origin_id = Column(Integer, ForeignKey(SCHEMA + '.origins.id'))
    group_id = Column(Integer, ForeignKey(SCHEMA + '.groups.id'))
    entity_id = Column(Integer, ForeignKey(SCHEMA + '.entities.id'))
    content_id = Column(Integer, ForeignKey(SCHEMA + '.contents.id'))
    time_created = Column(DateTime(timezone=True), server_default=func.now())
    time_updated = Column(DateTime(timezone=True), onupdate=func.now())

    group = relationship(Group, backref=backref("mentioned_origins"))
    origin = relationship(Origin, backref=backref("mentioned_by_groups"))
    entity = relationship(Entity, backref=backref("mentioned_by_groups"))
    content = relationship(Content, backref=backref("mentioned_by_groups"))


class ContentOrigins(Base):
    __tablename__ = 'contents_mentioned_origins'
    __table_args__ = {'schema': SCHEMA}
    id = Column(Integer, Sequence('content_mentioned_origins_id_seq', schema=SCHEMA), primary_key=True)
    content_id = Column(Integer, ForeignKey(SCHEMA + '.contents.id'))
    origin_id = Column(Integer, ForeignKey(SCHEMA + '.origins.id'))
    time_created = Column(DateTime(timezone=True), server_default=func.now())
    time_updated = Column(DateTime(timezone=True), onupdate=func.now())

    content = relationship(Content, backref=backref("contents", cascade="all, delete-orphan"))
    origin = relationship(Origin, backref=backref("origins", cascade="all, delete-orphan"))


class Analysis(Base):
    __tablename__ = 'analysis'
    __table_args__ = {'schema': SCHEMA}
    id = Column(Integer, Sequence('analysis_id_seq', schema=SCHEMA), primary_key=True)
    analysis_ts = Column(BigInteger)
    original_text = Column(String)
    translated_text = Column(String)
    original_language = Column(String)
    flag = Column(Integer)
    mana_assistant_score = Column(Float)
    location = Column(String)
    company = Column(String)
    company_match = Column(JSON)
    nlu_company_confidence = Column(Float)
    weighted_score_company = Column(Float)
    score = Column(Float)
    count = Column(Integer)
    text = Column(String)
    score_keywords_confirmed = Column(Integer)
    link = Column(String)
    status = Column(Enum(AnalysisStatus))
    status_exception = Column(String)
    type = Column(Enum(AnalysisType))
    expert_mana = Column(Enum(AnalysisExpertManaStatus), default=AnalysisExpertManaStatus.A_REVOIR)
    time_created = Column(DateTime(timezone=True), server_default=func.now())
    time_updated = Column(DateTime(timezone=True), onupdate=func.now())

    content_id = Column(Integer, ForeignKey(SCHEMA + '.contents.id'))
    content = relationship(Content, backref="analysis", foreign_keys=[content_id])

    def __str__(self):
        return "flag : {0}, mana_assistant_score : {1}, company : {2}".format(self.flag, self.mana_assistant_score, self.company)

class Company(Base):
    __tablename__ = 'companies'
    __table_args__ = {'schema': SCHEMA}
    name = Column(String, primary_key=True)
    time_created = Column(DateTime(timezone=True), server_default=func.now())
    time_updated = Column(DateTime(timezone=True), onupdate=func.now())

class CompanySynonym(Base):
    __tablename__ = 'company_synonyms'
    __table_args__ = {'schema': SCHEMA}
    name = Column(String, primary_key=True)
    company_name = Column(String, ForeignKey(SCHEMA + '.companies.name'))
    main_company = relationship(Company, foreign_keys=[company_name], backref=backref("synonyms", cascade="all, delete-orphan"))
    time_created = Column(DateTime(timezone=True), server_default=func.now())
    time_updated = Column(DateTime(timezone=True), onupdate=func.now())