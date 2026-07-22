from sf_utils import get_client
from sf_utils.db import get_connection, create_table_from_describe

conn = get_connection();
client = get_client();

create_table_from_describe(
    table_name="sf_attachment",
    sobject_type="Attachment",
    fields=["Id", "ParentId", "BodyLength", "ContentType", "CreatedDate", "Owner.Name", "OwnerId", "LastModifiedDate"],
    client=client,
    db_conn=conn
)