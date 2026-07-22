from sf_utils import get_client
from sf_utils.db import get_connection, create_table_from_describe

conn = get_connection();
client = get_client();

create_table_from_describe(
    table_name="sf_content_versions__c",
    sobject_type="ContentVersion",
    fields=["id", "Content_Type__c", "ContentDocumentId", "ContentSize", "CreatedById", "CreatedDate", "Department__c", "Disregard__c", "FileExtension", "FileType", "IsLatest", "LastModifiedById", "LastModifiedDate", "Origin", "OwnerId", "PublishStatus", "RecordType", "Name", "Title", "VersionNumber"],
    client=client,
    db_conn=conn
)