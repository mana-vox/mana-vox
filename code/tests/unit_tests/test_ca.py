import pytest

from content_analysis.main import analyse_content, analyse_contents
from mana_common import orm
from mana_common.shared import set_logger
from alchemy_mock.mocking import UnifiedAlchemyMagicMock

original_text = 'Toto est le roi de la déforestation'
translated_text = 'Toto is the king of deforestation'

# Fixture are function that can be injected into specific tests (passing them as parameter to the test)
# Or they can be run before all tests in this file, with the use of the autouse flag
@pytest.fixture(autouse=True)
def run_before_all_tests():
    # This is in order to enable the logs
    set_logger("ca_tests")


# All tests methods should be prefixed by "test_" so that pytest picked them up as tests
# The test name should be explicit enough for someone to understand what feature was broken

def test_dummy():
    assert(True is True)

def test_deforestation_evidence_return_analyse_with_flag_1(mocker):
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
    session = UnifiedAlchemyMagicMock()
    mocker.patch('mana_common.orm.get_session', return_value=session)
    # Mocking the various Watson Services
    # The output from nlu and assistant where captured by first running without the mock and logging the output
    mocker.patch('content_analysis.main.translate', return_value=['fr', translated_text])
    mocker.patch('content_analysis.main.nlu_analysis', return_value={'usage': {'text_units': 1, 'text_characters': 33, 'features': 5}, 'sentiment': {'document': {'score': -0.628943, 'label': 'negative'}}, 'language': 'en', 'keywords': [{'text': 'king of deforestation', 'sentiment': {'score': -0.628943, 'label': 'negative'}, 'relevance': 0.97332, 'emotion': {'sadness': 0.281308, 'joy': 0.319076, 'fear': 0.061622, 'disgust': 0.079251, 'anger': 0.159617}, 'count': 1}, {'text': 'Toto', 'sentiment': {'score': -0.628943, 'label': 'negative'}, 'relevance': 0.957921, 'emotion': {'sadness': 0.281308, 'joy': 0.319076, 'fear': 0.061622, 'disgust': 0.079251, 'anger': 0.159617}, 'count': 1}], 'entities': [], 'emotion': {'document': {'emotion': {'sadness': 0.281308, 'joy': 0.319076, 'fear': 0.061622, 'disgust': 0.079251, 'anger': 0.159617}}}, 'concepts': [{'text': 'Sneak King', 'relevance': 0.854345, 'dbpedia_resource': 'http://dbpedia.org/resource/Sneak_King'}, {'text': 'Stephen Curry', 'relevance': 0.779772, 'dbpedia_resource': 'http://dbpedia.org/resource/Stephen_Curry_(comedian)'}]})
    mocker.patch('content_analysis.main.send_message_to_assistant', mock_assistant_oui_mana)
    mocker.patch('mana_common.orm.get_companies_cache', return_value=[{'name': 'Toto', 'synonyms': ['Titi']}])

    analyses = analyse_content(content=orm.Content(value=original_text, content_type=orm.ContentType.tweet))
    assert len(analyses) == 1
    assert analyses[0].flag == 1

def test_analyse_contents_store_correct_number_of_analyses(mocker):
    # Mocking the database session
    session = UnifiedAlchemyMagicMock()

    # Adding two sample tweets in the mocked database
    session.add(orm.Content(value=original_text, content_type=orm.ContentType.tweet, analysis_ts=None))
    session.add(orm.Content(value=original_text, content_type=orm.ContentType.tweet, analysis_ts=None))
    mocker.patch('mana_common.orm.get_session', return_value=session)

    # Mocking the analyse_content function, that was already tested as part of the previous test
    mocker.patch('content_analysis.main.analyse_content', return_value=[orm.Analysis()])

    # We are really testing the analyse_contents() function here, making sure that it outputs a number of analysis matching the number of contents
    # TODO: we should also add a testcase where a tweet contains two companies, thus resulting in two analysis for a given content
    analyse_contents()
    assert 2 == len(session.query(orm.Analysis).all())

# Mocking assistant function
def mock_assistant_oui_mana(input_text, context):
    if input_text == translated_text:
        return {'output': {'text': ['OuiMANA'] }, 'intents': [{'intent': 'Oui_MANA', 'confidence': 0.35203494600761476}], 'context': {}}
    else:
        return {'intents': [{'intent': 'Oui_MANA', 'confidence': 0.35203494600761476}], 'entities': [{'entity': 'Deforestation', 'location': [8, 21], 'value': 'deforestation', 'confidence': 1}], 'input': {'text': 'king of deforestation'}, 'output': {'generic': [{'response_type': 'text', 'text': 'Alerting entity detected: king of deforestation'}], 'text': ['Alerting entity detected: king of deforestation'], 'nodes_visited': ['node_40_1545995367525'], 'log_messages': []}, 'context': {'metadata': {'user_id': 'mana-user'}, 'conversation_id': '298fc507-27b3-4fe7-aa4d-b8b511d400f2', 'system': {'initialized': True, 'dialog_stack': [{'dialog_node': 'node_40_1545995367525'}], 'dialog_turn_counter': 1, 'dialog_request_counter': 1, '_node_output_map': {'node_40_1545995367525': {'0': [0]}}}}, 'user_id': 'mana-user'}



