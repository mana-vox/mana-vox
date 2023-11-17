import pytest

import mana_common
from mana_common import orm
from mana_common.shared import set_logger
from alchemy_mock.mocking import UnifiedAlchemyMagicMock
from source_acquisition.main import add_rss_origin, add_web_origin, add_twitter_origin, \
    save_tweet_content, save_origin_content_relation, save_origin_group_relation,\
    process_tweets, process_twitter_reference, process_reference, process_tweets

from twitter import Url, User, Status

base_url = 'http://foeeurope.org/'  # for testing purpose
expected_rss_url = 'https://friendsoftheearth.eu/feed/'
tweet = "a sample tweet"


@pytest.fixture(autouse=True)
def run_before_all_tests(mocker):
    set_logger("sa_tests")
    # Mocking get_config which typically retrieves those values from db
    mocker.patch('mana_common.orm.get_config', return_value={
        "max_tweets": 10,
        "trusted_source_origins_threshold": 10,
        "trusted_source_group_threshold": 3,
        "source_candidate_threshold": 2,
        "use_nlu_for_company_detection": False,
        "url_extensions_to_check_for_true_url": [],
        "domains_where_next_element_matters": [],
        "ignore_for_rss_search": [],
        "url_patterns_to_ignore": [],
        "url_pattern_to_ignore_for_content_analysis": [],
        "social_keywords_pattern": ""
    })

def create_content(session):
    content = orm.Content(value="dummy text", content_type=orm.ContentType.tweet)
    session.add(content)
    return content


def create_origin(session):
    origin = orm.TwitterOrigin(screen_name="dummy", entity=orm.Entity(name="dummy"))
    session.add(origin)
    return origin


def test_add_rss_origin_finds_rss_and_set_occurences_to_1(mocker):
    session = UnifiedAlchemyMagicMock()
    mocker.patch('mana_common.orm.get_session', return_value=session)

    web_origin = orm.WebOrigin(id=1, raw_url=base_url, base_url=base_url)
    session.add(web_origin)
    add_rss_origin(web_origin)
    rss: orm.RssOrigin
    rss = session.query(orm.RssOrigin).filter(orm.RssOrigin.origins_web_id == 1).first()
    assert (rss is not None)
    assert (rss.base_url == web_origin.base_url)
    assert (rss.rss == expected_rss_url)
    assert (rss.occurrences == 1)


def test_add_rss_origin_increments_single_rss_origin_occurences_if_exists(mocker):
    session = UnifiedAlchemyMagicMock()
    mocker.patch('mana_common.orm.get_session', return_value=session)

    web_origin = orm.WebOrigin(id=1, raw_url=base_url, base_url=base_url)
    add_rss_origin(web_origin)

    web_origin_2 = orm.WebOrigin(id=2, raw_url=base_url, base_url=base_url)
    add_rss_origin(web_origin_2)

    rss: orm.RssOrigin
    rss = session.query(orm.RssOrigin).filter(orm.RssOrigin.base_url == base_url).first()
    assert (rss is not None)
    assert (rss.occurrences == 2)
    assert (session.query(orm.RssOrigin).filter(orm.RssOrigin.base_url == base_url).count() == 1)


def test_add_web_origin_creates_new_entity_and_origin_if_unknown_url(mocker):
    session = UnifiedAlchemyMagicMock()
    mocker.patch('mana_common.orm.get_session', return_value=session)

    content = orm.Content(value="dummy text with url mentioned: " + base_url, content_type=orm.ContentType.tweet)
    add_web_origin(content, Url(base_url=base_url, expanded_url=base_url))

    web_origin: orm.WebOrigin
    web_origin = session.query(orm.WebOrigin).filter(orm.WebOrigin.base_url == base_url).first()
    assert (web_origin is not None)
    assert (web_origin.entity is not None)
    assert (web_origin.occurrences == 1)


def test_add_web_origin_increments_single_web_origin_occurences_if_known_url(mocker):
    session = UnifiedAlchemyMagicMock()
    mocker.patch('mana_common.orm.get_session', return_value=session)

    content = orm.Content(value="dummy text with url mentioned: " + base_url, content_type=orm.ContentType.tweet)
    add_web_origin(content, Url(base_url=base_url, expanded_url=base_url))
    add_web_origin(content, Url(base_url=base_url, expanded_url=base_url))

    assert (session.query(orm.WebOrigin).filter(orm.WebOrigin.base_url == base_url).count() == 1)

    web_origin: orm.WebOrigin
    web_origin = session.query(orm.WebOrigin).filter(orm.WebOrigin.base_url == base_url).first()
    assert (web_origin.occurrences == 2)


def test_add_twitter_origin_creates_new_entity_and_origin_if_unknown_screen_name(mocker):
    session = UnifiedAlchemyMagicMock()
    mocker.patch('mana_common.orm.get_session', return_value=session)
    mocker.patch('source_acquisition.main.retrieve_and_associate_twitter_profile', return_value=None)

    content = orm.Content(value="dummy text with url mentioned: " + base_url, content_type=orm.ContentType.tweet)
    add_twitter_origin(content, User(screen_name="dummy"))

    twitter_origin: orm.TwitterOrigin
    twitter_origin = session.query(orm.TwitterOrigin).filter(orm.TwitterOrigin.screen_name == "dummy").first()
    assert (twitter_origin is not None)
    assert (twitter_origin.entity is not None)
    assert (twitter_origin.occurrences == 1)


def test_add_twitter_origin_increments_single_origin_if_known_screen_name(mocker):
    session = UnifiedAlchemyMagicMock()
    mocker.patch('mana_common.orm.get_session', return_value=session)
    mocker.patch('source_acquisition.main.retrieve_and_associate_twitter_profile', return_value=None)

    content = orm.Content(value="dummy text with url mentioned: " + base_url, content_type=orm.ContentType.tweet)
    add_twitter_origin(content, User(screen_name="dummy"))
    add_twitter_origin(content, User(screen_name="dummy"))

    assert (session.query(orm.TwitterOrigin).filter(orm.TwitterOrigin.screen_name == "dummy").count() == 1)

    twitter_origin: orm.TwitterOrigin
    twitter_origin = session.query(orm.TwitterOrigin).filter(orm.TwitterOrigin.screen_name == "dummy").first()
    assert (twitter_origin.occurrences == 2)


def test_save_tweet_content_associate_content_with_the_correct_origin(mocker):
    session = UnifiedAlchemyMagicMock()
    mocker.patch('mana_common.orm.get_session', return_value=session)

    origin = orm.TwitterOrigin(screen_name="dummy", entity=orm.Entity(name="dummy"))
    session.add(origin)

    content = save_tweet_content(tweet_content=orm.Content(value=tweet, content_type=orm.ContentType.tweet),
                                 reference_origin=origin)

    other_origin = orm.TwitterOrigin(screen_name="anotherAccount")
    session.add(other_origin)

    assert (session.query(orm.Content).filter(orm.Content.id == content.id).count() == 1)

    retrieved_content: orm.Content
    retrieved_content = session.query(orm.Content).filter(orm.Content.id == content.id).first()

    assert (retrieved_content is not None)
    assert (retrieved_content.origin_id == origin.id)
    assert (retrieved_content.origin.screen_name == "dummy")


def test_save_origin_content_relation_saves_a_new_record(mocker):
    session = UnifiedAlchemyMagicMock()
    mocker.patch('mana_common.orm.get_session', return_value=session)

    content = create_content(session)
    origin = create_origin(session)

    save_origin_content_relation(origin, content)

    assert (session.query(orm.ContentOrigins).count() == 1)


def test_save_origin_group_relation_saves_a_new_record(mocker):
    session = UnifiedAlchemyMagicMock()
    mocker.patch('mana_common.orm.get_session', return_value=session)

    content = create_content(session)
    origin = create_origin(session)

    entity = orm.Entity(name="Dummy", group=orm.Group(name="DummyGroup"))

    save_origin_group_relation(origin, entity, content)

    assert (session.query(orm.OriginGroup).count() == 1)


def test_process_tweets_calls_save_tweet_content_for_each_not_rt_tweets(mocker):
    session = UnifiedAlchemyMagicMock()
    mocker.patch('mana_common.orm.get_session', return_value=session)
    origin = create_origin(session)
    entity = orm.Entity(name="Dummy", group=orm.Group(name="DummyGroup"))
    session.add(entity)
    origin.entity = entity
    session.commit()

    nb_tweets = 2
    nb_rt_tweets = 3
    tweets = []
    for i in range(nb_tweets):
        tweets.append(Status(in_reply_to_status_id=None, full_text="dummy text",
                             urls=[Url(base_url=base_url), Url(base_url=base_url)],
                             user_mentions=[User(screen_name="Dummy"), User(screen_name="Dummy"), User(screen_name="Dummy")]))

    expected = len(tweets)
    print("expected = {}".format(expected))

    expected_add_web_origin = expected * 2
    expected_add_twitter_origin = expected * 3

    for i in range(nb_rt_tweets):
        tweets.append(Status(in_reply_to_status_id=True, full_text="dummy text", urls=[], user_mentions=[]))

    mock_save_tweet_content = mocker.patch('source_acquisition.main.save_tweet_content', return_value=orm.Content())
    mock_add_web_origin = mocker.patch('source_acquisition.main.add_web_origin', return_value=orm.WebOrigin())
    mock_add_twitter_origin = mocker.patch('source_acquisition.main.add_twitter_origin', return_value=orm.TwitterOrigin())

    process_tweets(reference_origin=origin, tweets=tweets)

    assert (mock_save_tweet_content.call_count == expected)
    assert (mock_add_web_origin.call_count == expected_add_web_origin)
    assert (mock_add_twitter_origin.call_count == expected_add_twitter_origin)


def test_process_reference_only_processes_twitter_origins(mocker):
    session = UnifiedAlchemyMagicMock()
    mocker.patch('mana_common.orm.get_session', return_value=session)

    mock_process_twitter_reference = mocker.patch('source_acquisition.main.process_twitter_reference',
                                                  return_value=None)

    ref_entity_with_twitter_origin = orm.Entity(name="Dummy")
    origin = create_origin(session)
    ref_entity_with_twitter_origin.origins.append(origin)
    session.add(ref_entity_with_twitter_origin)

    ref_entity_with_no_twitter_origin = orm.Entity(name="DummyNoTwitterOrigin")
    web_origin = orm.WebOrigin(base_url=base_url)
    ref_entity_with_no_twitter_origin.origins.append(web_origin)
    session.add(ref_entity_with_no_twitter_origin)

    process_reference(reference=ref_entity_with_twitter_origin)
    assert (mock_process_twitter_reference.call_count == 1)

    mock_process_twitter_reference.call_count = 0
    process_reference(reference=ref_entity_with_no_twitter_origin)
    assert (mock_process_twitter_reference.call_count == 0)


class MockTwApi:
    @staticmethod
    def GetUserTimeline(screen_name, since_id, count):
        return [Status(id=1, full_text="dummy tweet")]


def test_process_twitter_reference_returns_correct_number_of_tweets_and_set_origin_max_id(mocker):
    session = UnifiedAlchemyMagicMock()
    mocker.patch('mana_common.orm.get_session', return_value=session)

    mock_process_tweets = mocker.patch('source_acquisition.main.process_tweets',
                                       return_value=1)

    mana_common.shared._twitter_api = MockTwApi

    origin = create_origin(session)

    process_twitter_reference(origin)

    assert (mock_process_tweets.call_count == 1)
    assert (origin.last_synced_id == 1)


















