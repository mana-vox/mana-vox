
from fastapi import HTTPException

def check_excel_for_missing_columns(xlsx, columns):
    missing_columns = []
    for col in columns:
        if col not in xlsx.columns:
            missing_columns.append(col)
    if len(missing_columns) > 0:
        raise HTTPException(status_code=500, detail= { "missing_columns" : missing_columns } )

def list_duplicates(df, column):
    duplicates = df[df.duplicated(subset=[column],keep=False)][column].unique()
    duplicates = [x for x in duplicates if str(x) != 'nan' if str(x) != '' if x != None]
    return duplicates