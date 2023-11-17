from fastapi import HTTPException
import pandas as pd
import re
from mana_common.shared import log
from mana_common.orm import Company, CompanySynonym, Analysis
from api.utils import list_duplicates

company_name_column = "Company_Name"
synonym_column = "Synonyms"
created_column = "Created"
updated_column = "Updated"
added_synonyms_column = "Added_Synonyms"
deleted_synonyms_column = "Deleted_Synonyms"

def clean_company_value(comp):
    return re.sub("[\(\[].*?[\)\]]", "", comp).strip().replace('/', ',')

def check_companies_duplicates(df, company_name_column, synonym_column):
    errors = []
    company_names_duplicates = list_duplicates(df, company_name_column)
    synonyms_duplicates = list_duplicates(df, synonym_column)
    all_df = pd.DataFrame(get_all_values(df, company_name_column, synonym_column), columns=['values'])
    all_duplicates = list_duplicates(all_df, 'values')

    if len(company_names_duplicates) > 0:
        errors.append({ "error" : "There are some duplicates in the " + company_name_column + " column", "duplicates" : company_names_duplicates })
    if len(synonyms_duplicates) > 0:
        errors.append({ "error" : "There are some duplicates in the " + synonym_column + " column", "duplicates" : synonyms_duplicates })
    if len(all_duplicates) > 0:
        errors.append({ "error" : "There are some duplicates across the two columns", "duplicates" : all_duplicates })

    if len(errors) > 0:
        raise HTTPException(status_code=409, detail={"errors": errors})


def get_all_values(df, company_column, synonym_colum):
    values = list(set(df[company_column].values))
    synonyms = []
    for idx, row in df.iterrows():
        if not pd.isna(row.Synonyms):
            for syn in row[synonym_colum].split(","):
                synonyms.append(syn)
    values = values + list(set(synonyms))
    return values

def delete_companies_not_used_by_analysis(db):
    deleted = []
    companies_to_be_updated_only = []
    for a, c in db.query(Analysis, Company).filter(Analysis.company == Company.name).all():
        companies_to_be_updated_only.append(c.name)

    companies_to_be_updated_only = list(set(companies_to_be_updated_only))

    to_be_deleted = db.query(Company).filter(~Company.name.in_(companies_to_be_updated_only))
    for c in to_be_deleted:
        log.info("Deleting {}".format(c.name))
        db.delete(c)
        deleted.append(c.name)

    return deleted, companies_to_be_updated_only

def process_companies(db, df):
    for index, row in df.iterrows():
        company_name = clean_company_value(df.at[index, company_name_column])
        log.info("COMPANY {0}".format(company_name))
        synonyms = None
        if not pd.isna(df.at[index, synonym_column]):
            synonyms = clean_company_value(df.at[index, synonym_column]).split(",")

        main_company = db.query(Company).filter(Company.name == company_name).first()
        if main_company is None:
            log.info("To be created")
            main_company = Company(name=company_name)
            db.add(main_company)
            df.at[index, created_column]=1
        else:
            log.info("To be updated")
            df.at[index, updated_column]=1
            synonyms_to_be_deleted = db.query(CompanySynonym).filter(CompanySynonym.company_name == company_name)
            for s in synonyms_to_be_deleted:
                db.delete(s)
                try:
                    df.at[index, deleted_synonyms_column] = df.at[index, deleted_synonyms_column] + "," + s.name
                except:
                    df.at[index, deleted_synonyms_column] = s.name

        if synonyms != None:
            for s in synonyms:
                log.info("Synonym {0}".format(s))
                db.add(CompanySynonym(name=s, main_company=main_company))
                try:
                    df.at[index, added_synonyms_column] = df.at[index, added_synonyms_column] + "," + s
                except:
                    df.at[index, added_synonyms_column] = s
                    pass
        else:
            log.info("No synonym provided")

    return df

def compute_companies_kpis(df, deleted_companies, company_cant_delete):
    cant_delete = list(set(company_cant_delete) - set(list(df[company_name_column])))
    added_companies = list(df[df[created_column] == 1][company_name_column]) if created_column in df.columns else []
    really_deleted_companies = list(set(deleted_companies) - set(added_companies))
    really_added_companies = list(set(added_companies) - set(deleted_companies))
    fully_updated = list(set(added_companies).intersection(deleted_companies))
    synonym_only_updated = list(df[df[updated_column] == 1][company_name_column]) if updated_column in df.columns else []
    updated = fully_updated + synonym_only_updated

    return { 
        "nb_cant_delete_companies" : len(cant_delete),
        "nb_deleted_companies": len(really_deleted_companies),
        "nb_added_companies" : len(really_added_companies),
        "nb_updated_companies" : len(updated),
        "cant_delete_companies": cant_delete,
        "deleted_companies" : really_deleted_companies,
        "added_companies" : really_added_companies,
        "updated_companies" : updated,
    }

def delete_company_from_db(db, name):
    query = db.query(Analysis).filter(Analysis.company == name)
    nb_analysis_deleted = query.count()
    log.info("Will delete {} analysis records".format(nb_analysis_deleted))
    query.delete()
    query = db.query(CompanySynonym).filter(CompanySynonym.main_company.has(Company.name == name))
    nb_synonyms_deleted = query.count()
    log.info("Will delete {} synonyms".format(nb_synonyms_deleted))
    query.delete(synchronize_session='fetch')
    db.query(Company.name).filter(Company.name == name).delete()
    db.commit()
    log.info("Deleted {}".format(name))
    return nb_analysis_deleted, nb_synonyms_deleted