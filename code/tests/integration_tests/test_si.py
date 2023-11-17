from sqlalchemy.sql.ddl import DropSchema
import pytest
from mana_common import orm, shared
from sqlalchemy import and_
import os
import requests
import time


# In those (integration) tests, we are no longer mocking the watson and db services
# Thus, those are to be ran against a dedicated integration environment, in order not to mess with the prod data
# The details of the integration environment is to be specified in the pipeline env variables, or in the .env file
# when running locally

@pytest.fixture(autouse=True)
def run_before_all_tests():
    # This is in order to enable the logs
    shared.set_logger("integration_tests")


# This test could be used to verify if the cloud function does create properly the schema and tables
# the first time it is ran
# Commenting it out here, since there is a single env, we don't want to drop the prod db by mistake
'''def test_source_identification_creates_tables_if_no_schema():
    orm.setup_db(os.environ["DB_CONNECTION_STRING"])
    orm.get_db_engine().execute(DropSchema(orm.SCHEMA, cascade=True))
    launch_cloud_action_si()
    assert orm.get_db_engine().dialect.has_table(orm.get_db_engine(), orm.Content.__tablename__, schema=orm.SCHEMA)'''


def test_source_identification_retrieves_the_records():
    # We want to verify if the source identification module does retrieve and store a matching number of tweets
    # Ideally we should create a new source (test twitter account) and publish a predefined tweet
    # that we can verify in db after running the SI cloud function
    # Since we don't have a twitter test account to use here, we are removing the existing tweets in db for a source
    # Then we are querying the twitter api to see how many records we are supposed to retrieve (should be MAX_TWEETS)
    # Then running the cloud function
    # Then verifying that the output in db match the expected number
    print(os.environ["INTEGRATION_DB_CONNECTION_STRING"])
    orm.setup_db(os.environ["INTEGRATION_DB_CONNECTION_STRING"])
    origin_id = clean_tweets_from_first_twitter_origin()
    expected_new_records = count_expected_tweets_for_origin(origin_id)
    print("Origin id {} : we expect to retrieve {} contents".format(origin_id, expected_new_records))
    if expected_new_records > 0:
        launch_cloud_action_si()
        total = check_total_records_for_origin(origin_id)
        print("Origin id {} has now {} contents, comparing {} with {}".format(origin_id, total, total, expected_new_records))
        assert total == expected_new_records
    else:
        raise Exception ("not launching si cloud action since there are no new records")


def get_iam_token():
    resp = requests.post("https://iam.cloud.ibm.com/identity/token",
                         {"grant_type": "urn:ibm:params:oauth:grant-type:apikey", "apikey": os.environ["IAM_APIKEY"]})
    print(resp.json())
    return resp.json()["access_token"]


def clean_tweets_from_first_twitter_origin():
    origin = orm.get_session().query(orm.TwitterOrigin).join(orm.Entity).filter(and_(orm.Entity.is_reference == True, orm.Entity.status == orm.EntityStatus.SOURCE)).first()
    if origin is not None:
        content: orm.Content
        print("{} contents to be deleted".format(len(origin.contents)))
        for content in origin.contents:
            orm.get_session().query(orm.OriginGroup).filter(orm.OriginGroup.content_id == content.id).delete()
            orm.get_session().query(orm.ContentOrigins).filter(orm.ContentOrigins.content_id == content.id).delete()
            orm.get_session().delete(content)
        origin.last_synced_id = None
        orm.get_session().commit()
        return origin.id
    else:
        raise Exception("Not able to delete tweets")


def count_expected_tweets_for_origin(origin_id):
    origin = orm.get_session().query(orm.TwitterOrigin).filter(orm.TwitterOrigin.id == origin_id).first()
    if origin is not None:
        origin: orm.TwitterOrigin
        print(origin.last_synced_id)
        print("Current contents in db : {}".format(len(origin.contents)))
        tweets = shared.twitter_api().GetUserTimeline(
            screen_name=origin.screen_name, since_id=origin.last_synced_id, count=os.environ["MAX_TWEETS"]
        )
        return len(tweets)

    else:
        raise Exception("Origin does not exist")


def check_total_records_for_origin(origin_id):
    origin = orm.get_session().query(orm.TwitterOrigin).filter(orm.TwitterOrigin.id == origin_id).first()
    if origin is None:
        return 0
    else:
        return len(origin.contents)


def query_cloud_function_si(activation_id, token):
    print("query_cloud_function_si activation_id = {}".format(activation_id))
    resp = requests.get(
        "https://eu-de.functions.cloud.ibm.com/api/v1/namespaces/" + os.environ["INTEGRATION_CLOUD_FUNCTION_NAMESPACE"] + "/activations/" + activation_id,
        headers={"Authorization": "Bearer " + token, "X-Require-Whisk-Auth": os.environ["OPENWHISK_KEY"]})
    return resp.status_code


def launch_cloud_action_si():
    token = get_iam_token()
    resp = requests.post(os.environ["SI_CLOUD_FUNCTION_URL"], headers={"X-Require-Whisk-Auth": os.environ["OPENWHISK_KEY"], "Authorization": "Bearer " + token })
    print(resp.text)
    print(resp.json())
    activation_data = resp.json()
    activation_id = activation_data["activationId"]
    print(activation_id)
    resp = query_cloud_function_si(activation_id, token)
    while resp.status_code != 200:
        resp = query_cloud_function_si(activation_id, token)
        time.sleep(10)
