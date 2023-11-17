from fastapi import FastAPI, File, UploadFile, Depends, HTTPException, Security
import secrets
from fastapi.responses import StreamingResponse
import io
import pandas as pd
import os
from mana_common.shared import log, get_twitter_infos, set_logger
from mana_common.orm import setup_db, get_session, get_db_engine, SCHEMA
from mana_common.orm import Company, CompanySynonym, merge_entities, load_cache_companies, ContentType, TwitterOrigin
from api.tests import analyse_test_contents, extract_rss_articles, extract_content_from_url
from api.auth import any_role, Role
from api.companies import check_companies_duplicates, delete_companies_not_used_by_analysis, process_companies, \
    compute_companies_kpis, company_name_column, synonym_column, delete_company_from_db
from api.utils import check_excel_for_missing_columns
from api.sources import check_sources_duplicates, source_name_column, group_column, is_reference_column, \
    twitter_column, web_column, location_column, ecoregion_column, tags_column, is_trusted_column, process_sources, compute_sources_kpis
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from api.data import create_companies_df, create_sources_df, create_analysis_df
from typing import List
from api.dto import ContentDto, RssFeedUrlDto, CompanyDto
from datetime import date


OPENAPI_TITLE = "MANA-VOX Api"
OPENAPI_VERSION = "0.1.2"


app = None
swagger_auth_username = None
swagger_auth_password = None
basic_auth = None


def init_api():
    global app, swagger_auth_username, swagger_auth_password

    # Init dependencies
    set_logger("api")

    # Init database
    db_connection_string = os.environ.get("DB_CONNECTION_STRING")
    log.info("Using {} for database connection string".format(db_connection_string))
    if not db_connection_string:
        raise Exception("No DB_CONNECTION_STRING defined in environment variables")
    setup_db(db_connection_string)
    if not get_db_engine().dialect.has_schema(get_db_engine(), SCHEMA):
        raise Exception("Schema {} not found".format(SCHEMA))
    log.info("Will use schema: {}".format(SCHEMA))
    Company.__table__.create(bind=get_db_engine(), checkfirst=True)
    CompanySynonym.__table__.create(bind=get_db_engine(), checkfirst=True)

    load_cache_companies()

    # Basic auth for protecting Swagger
    swagger_auth_username = os.environ.get("SWAGGER_AUTH_USERNAME")
    swagger_auth_password = os.environ.get("SWAGGER_AUTH_PASSWORD")
    if swagger_auth_username and swagger_auth_password:
        log.info("Basic auth is set for protecting Swagger: {}/{}".format(swagger_auth_username, swagger_auth_password))
        global basic_auth
        basic_auth = HTTPBasic()
    else:
        log.info("Basic auth is NOT set for protecting Swagger")

    # Init FastAPI
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)  # we'll use our own routes for Swagger


# Init API now
init_api()


def check_basic_auth(credentials):
    log.info('')
    if not swagger_auth_username or not swagger_auth_password:
        return
    correct_username = secrets.compare_digest(credentials.username, swagger_auth_username)
    correct_password = secrets.compare_digest(credentials.password, swagger_auth_password)
    log.info("User='{}' Vs. '{}'; Password='{}' Vs. '{}'".format(credentials.username, swagger_auth_username, credentials.password, swagger_auth_password))
    if not correct_username or not correct_password:
        raise HTTPException(
            status_code=401,
            detail="Incorrect credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


@app.get("/openapi.json", include_in_schema=False)
async def get_open_api_endpoint(credentials: HTTPBasicCredentials = None if basic_auth is None else Depends(basic_auth)):
    check_basic_auth(credentials)
    return JSONResponse(get_openapi(title=OPENAPI_TITLE, version=OPENAPI_VERSION, routes=app.routes))


@app.get("/docs", include_in_schema=False)
async def get_documentation(credentials: HTTPBasicCredentials = None if basic_auth is None else Depends(basic_auth)):
    check_basic_auth(credentials)
    return get_swagger_ui_html(openapi_url="/openapi.json", title=OPENAPI_TITLE)


@app.post(
    "/companies",
    tags=["Companies"],
    summary="Updates the list of companies",
    description="Takes an Excel (`.xslx`) file as input to update the list of companies and their synonyms "
                + "that the MANA engine will use to identify incidents.<br><br>"
                + "**Note:** the system will take the Excel file as the new reference for companies, leading to "
                + "potential **removal** and/or **replacement** for the companies existing in the system before the "
                + "import.",
    dependencies=[Security(any_role([Role.ADMIN]))]
)
async def upload_companies(file: UploadFile = File(...)):
    log.info("Loading {}".format(file.filename))
    if not file.filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Expecting an xlsx file.")

    xlsx = pd.read_excel(file.file)
    check_excel_for_missing_columns(xlsx, [company_name_column, synonym_column])
    check_companies_duplicates(xlsx, company_name_column, synonym_column)
    deleted_companies, company_cant_delete = delete_companies_not_used_by_analysis(get_session())

    df = process_companies(get_session(), xlsx)

    log.info("Commit to db")
    get_session().commit()

    return compute_companies_kpis(df, deleted_companies, company_cant_delete)

@app.get(
    "/companies",
    tags=["Companies"],
    summary="Get the current company list",
    dependencies=[Security(any_role([Role.ADMIN]))]
)
async def get_companies():
    try:
        return await get_companies_wrapped()
    except Exception as e:
        log.error('******************************** => Exception')
        log.exception(e)
        raise


async def get_companies_wrapped():
    companies = {}
    companiesDto = []
    comp = get_session().query(Company.name, CompanySynonym.name).join(CompanySynonym, isouter=True).all()
    for c, s in comp:
        log.info(" c: {}, s: {}".format(c,s))
        if c not in companies:
            companies[c] = CompanyDto(name=c, synonyms=[])
            if s is not None:
                companies[c].synonyms.append(s)
        elif s is not None:
                companies[c].synonyms.append(s)

    for c in companies.keys():
        companiesDto.append(companies.get(c))

    return companiesDto

@app.delete(
    "/companies/{name}",
    tags=["Companies"],
    summary="Get the current company list",
    dependencies=[Security(any_role([Role.ADMIN]))]
)
async def delete_company(name: str):
    log.info("delete_company {}".format(name))
    nb_analysis_deleted, nb_synonyms_deleted = delete_company_from_db(get_session(), name)
    return { "deleted_company" : name, "nb_synonyms_deleted": nb_synonyms_deleted, "nb_analysis_deleted" : nb_analysis_deleted }


@app.post(
    "/sources",
    tags=["Sources"],
    summary="Updates the list of sources",
    description="Takes an Excel (`.xslx`) file as input to update the list of sources that the MANA engine will use "
                + "to identify incidents.<br><br>"
                + "**Note:** the system will take the Excel file as the new reference for sources, leading to "
                + "potential **removal** and/or **replacement** for the sources existing in the system before the "
                + "import.",
    dependencies=[Security(any_role([Role.ADMIN]))]
)
async def upload_sources(file: UploadFile = File(...)):
    log.info("Loading {}".format(file.filename))
    if not file.filename.endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Expecting an xlsx file.")

    # Read xlsx file and check mandatory columns and twitter/web duplicates
    xlsx = pd.read_excel(file.file)
    # replace nan values with None (accepted by SQL)
    xlsx = xlsx.where(pd.notnull(xlsx), None)
    check_excel_for_missing_columns(xlsx, [source_name_column, group_column, is_reference_column, twitter_column, web_column, is_trusted_column, location_column, ecoregion_column, tags_column])
    check_sources_duplicates(xlsx)

    df = process_sources(get_session(), xlsx)

    log.info("Commit to db")
    get_session().commit()

    return compute_sources_kpis(df)


@app.post(
    "/entities/{id1}/merge/{id2}",
    tags=["Entities"],
    summary="Merge two entities",
    description="Merge entity `id2` (and delete it) into the entity `id1` (unless `id2` is a reference entity, in such "
                + "case `id1` will be merged into `id2`).",
    dependencies=[Security(any_role([Role.ADMIN]))]
)
async def merge_two_entities(id1: int, id2: int, comment: str):
    log.info("Will merge entities {} and {}".format(id1, id2))

    success, message = merge_entities(id1, id2, comment)

    if not success:
        raise HTTPException(status_code=409, detail={"error": message})

    get_session().commit()
    return {"message": message}


@app.post(
    "/entities/twitter_location",
    tags=["Entities"],
    summary="Get location from twitter",
    description="Get all locations for twitter origins that do not have a location yet",
    dependencies=[Security(any_role([Role.ADMIN]))]
)
async def get_twitter_location():
    query = get_session().query(TwitterOrigin).filter(TwitterOrigin.location == None)
    origins_unlocated = query.all()
    log.info("Getting twitter locations for {} origins".format(len(origins_unlocated)))
    # To store deleted or suspended acount and send them back to the user
    deleted_or_suspended = []

    # Rate limit for user lookups : 300 / 15 minutes
    for origin in origins_unlocated[:200]:
        url, _, location = get_twitter_infos(origin.screen_name, [])
        if location != None:
            origin.location = location # error for deleted account dealt with in get_twitter_infos

        # Handles suspended or deleted account or a technical error after moving the try except error in get_twitter_infos
        if url == None:
            log.error("Twitter account {} triggered a twitter error".format(origin.screen_name))
            origin.valid_extraction = False
            deleted_or_suspended.append(origin.screen_name)

    get_session().commit()
    return {
        "updated locations": min(len(origins_unlocated), 200),
        "locations left to update": len(origins_unlocated) - min(len(origins_unlocated), 200),
        "nb_deleted_or_suspended_account": len(deleted_or_suspended),
        "deleted_or_suspended_account": deleted_or_suspended
    }

@app.get(
    "/data",
    tags=["Data"],
    summary="Download data",
    description="Download database data : Companies and Synonyms, Sources, Analysis/Incidents",
    dependencies=[Security(any_role([Role.ADMIN]))]
)
async def download_data(startDate: date, endDate: date):
    output = io.BytesIO()
    writer = pd.ExcelWriter(output, engine='xlsxwriter', options = {'remove_timezone': True})

    df = create_companies_df(get_db_engine(), startDate, endDate)
    df.to_excel(writer, sheet_name="Companies", index=False)

    df = create_sources_df(get_db_engine(), startDate, endDate)
    df.to_excel(writer, sheet_name="Sources", index=False)

    df = create_analysis_df(get_db_engine(), startDate, endDate)
    df.to_excel(writer, sheet_name="Incidents", index=False)

    writer.close()

    output.seek(0)

    headers = {
         'Content-Disposition': 'attachment; filename="mana.xlsx"'
    }
    return StreamingResponse(output, headers=headers)


@app.post(
    "/tests/tweets/ca",
    tags=["Tests"],
    summary="Submit tweets for analysis",
    description="A way to quickly check for possible analysis regressions",
    dependencies=[Security(any_role([Role.ADMIN]))]
)
async def submit_tweets(texts: List[ContentDto]):
    log.info("Will submit {} texts for content analysis".format(len(texts)))
    return {"analyses": analyse_test_contents(texts, content_type=ContentType.tweet) }

@app.post(
    "/tests/rss/ca",
    tags=["Tests"],
    summary="Submit rss articles for analysis",
    description="A way to quickly check for possible analysis regressions",
    dependencies=[Security(any_role([Role.ADMIN]))]
)
async def submit_rss_articles(texts: List[ContentDto]):
    log.info("Will submit {} texts for content analysis".format(len(texts)))
    return {"analyses": analyse_test_contents(texts, content_type=ContentType.rss) }

@app.post(
    "/tests/rss/extractfeeds",
    tags=["Tests"],
    summary="Extract rss articles from a particular feed",
    description="A way to quickly check for possible analysis regressions",
    dependencies=[Security(any_role([Role.ADMIN]))]
)
async def extract_articles(rss_feed: RssFeedUrlDto):
    log.info("Will submit {} this rss feed for extraction".format(rss_feed))
    maxts, articles = extract_rss_articles(rss_feed)
    return {"nb_articles" : len(articles),  "extracted_articles": articles }

@app.post(
    "/tests/web/extractcontent",
    tags=["Tests"],
    summary="Extract content from website",
    dependencies=[Security(any_role([Role.ADMIN]))]
)
async def extract_content(websiteUrl: str):
    log.info("Will submit {} this web site for extraction".format(websiteUrl))
    content = extract_content_from_url(websiteUrl)
    return {"extracted" : content }
