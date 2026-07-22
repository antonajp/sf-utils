from sf_utils import get_client
from sf_utils.db import get_connection, create_table_from_describe

conn = get_connection();
client = get_client();

create_table_from_describe(
    table_name="sf_content_document__c",
    sobject_type="ContentDocument",
    fields=["id", "ArchivedById", "ArchivedDate", "ContentAssetId", "ContentModifiedDate", "ContentSize", "ContentSizeLong", "CreatedById", "CreatedDate", "FileExtension", "FileType", "LastModifiedById", "LastModifiedDate", "LastReferencedDate", "LastViewedDate", "OwnerId", "ParentId", "PublishStatus", "Title"],
    client=client,
    db_conn=conn
)