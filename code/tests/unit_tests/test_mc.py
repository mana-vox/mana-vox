import pytest
import re

from mana_common import orm
from mana_common.shared import set_logger
from alchemy_mock.mocking import UnifiedAlchemyMagicMock

translated_text = 'The companies most responsible for the deforestation are Cargill, Noble, TotalEnergies, BPI, Asia P&P, CVBP, APPI and Mars'
companies = [{"name": "Cargill Incorporated", "synonyms": ["Cargill"]}, {"name": "Nobel", "synonyms": ["Noble"]}, {"name": "Total", "synonyms": ["TotalEnergies"]}, {"name": "BP", "synonyms": ["British Petroleum"]}, {"name": "Asia Pulp & Paper", "synonyms": ["APP", "Asia P&P"]}, {"name": "Mars", "synonyms": []}]

@pytest.fixture(autouse=True)
def run_before_all_tests(mocker):
    # This is in order to enable the logs
    set_logger("mc_tests")

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


def test_find_companies_returns_correct_number_of_companies(mocker):
    mocker.patch('mana_common.orm.get_companies_cache', return_value=companies)
    matching_companies = orm.find_companies(translated_text)
    print("companies found: {}".format(matching_companies))
    
    for company in matching_companies:
        for detail in company['match_details']: # if more than one faulty match for the same company, may stop after the first one -- check
            if (detail['matched'] in ['Cargill'] or len(detail['matched']) <= 5):
                print('detail: {}'.format(detail['matched']))
                if not re.compile(r'\b({0})\b'.format(detail['matched'])).search(translated_text):
                    print('{} should be removed'.format(detail['matched']))
                    company['match_details'].remove(detail)
                    if detail['matched'] in company['synonyms']:
                        company['synonyms'].remove(detail['matched'])

    for company in matching_companies:
        if len(company['match_details']) == 0:
            print('no more company match for: {}'.format(company))
            matching_companies.remove(company)
    print("companies found: {}".format(matching_companies))

    #assert (len(matching_companies) == 4)