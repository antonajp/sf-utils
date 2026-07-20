#!/usr/bin/env python3
"""Example: Create, read, update, and delete a Contact.

Usage:
    python examples/crud_contact.py

This script demonstrates the full CRUD lifecycle using sf_utils.
"""

from sf_utils import get_client
from sf_utils.sobjects import create_record, get_record, update_record, delete_record


def main():
    client = get_client()
    print(f"Connected to: {client.sf_instance}\n")

    # Create a contact
    print("Creating contact...")
    contact_data = {
        "FirstName": "Test",
        "LastName": "Example",
        "Email": "test.example@example.com",
    }
    result = create_record("Contact", contact_data, client=client)
    contact_id = result["id"]
    print(f"  Created: {contact_id}")

    # Read the contact back
    print("\nReading contact...")
    contact = get_record("Contact", contact_id, client=client)
    print(f"  Name: {contact['FirstName']} {contact['LastName']}")
    print(f"  Email: {contact['Email']}")

    # Update the contact
    print("\nUpdating contact...")
    update_record("Contact", contact_id, {"Title": "Developer"}, client=client)
    print("  Added title: Developer")

    # Verify update
    contact = get_record("Contact", contact_id, client=client)
    print(f"  Title is now: {contact['Title']}")

    # Delete the contact
    print("\nDeleting contact...")
    delete_record("Contact", contact_id, client=client)
    print(f"  Deleted: {contact_id}")

    print("\nCRUD lifecycle complete!")


if __name__ == "__main__":
    main()
