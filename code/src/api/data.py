import pandas as pd
import numpy as np
from mana_common.orm import Company,  CompanySynonym, Entity, TwitterOrigin, WebOrigin, RssOrigin, Analysis, Content, \
    Group, Origin, SCHEMA
from mana_common.shared import log

def remove_tz_from_datetime_before_export(df):
    #log.info(df.dtypes)
    for index_column in range(len(df.columns)):
        col = df.columns[index_column]
        if "time_created" in col or "time_updated" in col:
            df.iloc[:,index_column] = df.iloc[:,index_column].astype('datetime64[ns]')
            df.iloc[:,index_column] = df.iloc[:,index_column].dt.tz_localize(None)
    return df

def create_companies_df(engine, startDate, endDate):
    log.info("Extract companies created between {} and {}".format(startDate, endDate))
    sql = "select c.name as company_name, cs.name as synonym "\
        + "from " + SCHEMA + "." + Company.__tablename__ + " as c "\
        + "left join " + SCHEMA + "." + CompanySynonym.__tablename__ + " as cs "\
        + "ON c.name = cs.company_name "\
        + "where c.time_created >= '" + str(startDate) + "' and c.time_created <= '" + str(endDate) + "'"

    df = pd.read_sql_query(sql=sql, con=engine)

    # the sql returns multiple rows per company (one per synonym). Merging them, and removing the empty arrays created durng the merge
    df = df.replace([None], "")
    df = df.groupby('company_name')['synonym'].apply(list).reset_index(name='synonyms')
    df['synonyms'] = df['synonyms'].apply(lambda cell: ",".join([s for s in cell ]))

    df = remove_tz_from_datetime_before_export(df)
    return df

def create_sources_df(engine, startDate, endDate):
    log.info("Extract sources created between {} and {}".format(startDate, endDate))
    sql = "select * from (select e.id as source_id, e.name as source_name, e.time_created, e.is_reference, e.trusted, e.status, e.merges, e.suggested_merges, e.t_occurrences, e.t_source_references, e.tags, e.match_done, e.group_id, o.id as origin_id " \
        + "from " + SCHEMA + "." + Entity.__tablename__ + " as e, " + SCHEMA + "." + Origin.__tablename__ + " as o, " \
        + SCHEMA + "." + Group.__tablename__ + " as g " \
        + "where o.entity_id = e.id) as sources " \
        + "left join " + SCHEMA + "." + Group.__tablename__ + " as g " \
        + "ON sources.group_id = g.id " \
        + "left join " + SCHEMA + "." + TwitterOrigin.__tablename__ + " as t " \
        + "ON sources.origin_id = t.id " \
        + "left join " + SCHEMA + "." + RssOrigin.__tablename__ + " as r " \
        + "on sources.origin_id=r.id " \
        + "left join " + SCHEMA + "." + WebOrigin.__tablename__ + " as w " \
        + "on sources.origin_id=w.id "\
        + "where sources.time_created >= '" + str(startDate) + "' and sources.time_created <= '" + str(endDate) + "'"

    df = pd.read_sql_query(sql=sql, con=engine)

    # the sql returns multiple rows by source : one per origin id. So "merging them" to keep the cells with non NaN values
    df = df.replace([None], np.nan)
    df = df.groupby('source_id').first().reset_index()
    
    df = remove_tz_from_datetime_before_export(df)

    return df

def create_analysis_df(engine, startDate, endDate):
    log.info("Extract incidents created between {} and {}".format(startDate, endDate))
    sql = "select * from " + SCHEMA + "." + Analysis.__tablename__ + " as a, " + SCHEMA + "." \
          + Content.__tablename__ + " as c" + " where a.content_id = c.id and a.time_created >= '" + str(startDate) + "' and a.time_created <= '" + str(endDate) + "'"
    df = pd.read_sql_query(sql=sql, con=engine)
    
    df = remove_tz_from_datetime_before_export(df)

    return df


