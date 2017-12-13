# -*- coding: utf-8 -*-
import fauxfactory
import pytest

from cfme import test_requirements
from cfme.services.catalogs.ansible_catalog_item import AnsiblePlaybookCatalogItem
from cfme.utils.appliance.implementations.ui import navigate_to
from cfme.utils.update import update
from cfme.utils.version import current_version
from cfme.utils.wait import wait_for

pytestmark = [
    pytest.mark.long_running,
    pytest.mark.meta(server_roles=["+embedded_ansible"]),
    pytest.mark.uncollectif(lambda: current_version() < "5.8"),
    pytest.mark.ignore_stream("upstream"),
    test_requirements.ansible
]


CREDENTIALS = [
    (
        "Machine",
        {
            "username": fauxfactory.gen_alpha(),
            "password": fauxfactory.gen_alpha(),
            "privilage_escalation": "sudo",
            "privilage_escalation_username": fauxfactory.gen_alpha(),
            "privilage_escalation_password": fauxfactory.gen_alpha(),
            "vault_password": fauxfactory.gen_alpha()
        }
    ),
    (
        "Scm",
        {
            "username": fauxfactory.gen_alpha(),
            "password": fauxfactory.gen_alpha(),
        }
    ),
    (
        "Amazon",
        {
            "access_key": fauxfactory.gen_alpha(),
            "secret_key": fauxfactory.gen_alpha(),
            "sts_token": fauxfactory.gen_alpha()
        }
    ),
    (
        "VMware",
        {
            "username": fauxfactory.gen_alpha(),
            "password": fauxfactory.gen_alpha(),
            "vcenter_host": fauxfactory.gen_alpha()
        }
    )
]

REPOSITORIES = [
    "https://github.com/quarckster/ansible_playbooks",
    "https://github.com/patchkez/ansible_playbooks"
]


@pytest.fixture(scope="module")
def wait_for_ansible(appliance):
    appliance.wait_for_embedded_ansible()


@pytest.fixture(scope="module")
def action_collection(appliance):
    return appliance.collections.actions


@pytest.fixture(scope="module")
def credentials_collection(appliance):
    return appliance.collections.ansible_credentials


@pytest.yield_fixture(scope='module')
def ansible_repository(appliance):
    repositories = appliance.collections.ansible_repositories
    repository = repositories.create(
        fauxfactory.gen_alpha(),
        REPOSITORIES[0],
        description=fauxfactory.gen_alpha())
    wait_for(
        lambda: repository.get_detail("Properties", "Status", refresh=True) == "successful",
        timeout=60
    )
    yield repository

    if repository.exists:
        repository.delete()


@pytest.yield_fixture(scope="module")
def catalog_item(ansible_repository):
    cat_item = AnsiblePlaybookCatalogItem(
        fauxfactory.gen_alphanumeric(),
        fauxfactory.gen_alphanumeric(),
        provisioning={
            "repository": ansible_repository.name,
            "playbook": "dump_all_variables.yml",
            "machine_credential": "CFME Default Credential",
            "create_new": True,
            "provisioning_dialog_name": fauxfactory.gen_alphanumeric()
        }
    )
    cat_item.create()
    yield cat_item

    if cat_item.exists:
        cat_item.delete()


@pytest.mark.tier(1)
def test_embedded_ansible_repository_crud(ansible_repository, wait_for_ansible):
    updated_description = "edited_{}".format(fauxfactory.gen_alpha())
    with update(ansible_repository):
        ansible_repository.description = updated_description
    view = navigate_to(ansible_repository, "Edit")
    wait_for(lambda: view.description.value != "", delay=1, timeout=5)
    assert view.description.value == updated_description


@pytest.mark.tier(1)
@pytest.mark.parametrize(("credential_type", "credentials"), CREDENTIALS,
    ids=[cred[0] for cred in CREDENTIALS])
def test_embedded_ansible_credential_crud(credentials_collection, wait_for_ansible, credential_type,
        credentials, appliance):
    credential = credentials_collection.create(
        "{}_credential_{}".format(credential_type, fauxfactory.gen_alpha()),
        credential_type,
        **credentials
    )
    updated_value = "edited_{}".format(fauxfactory.gen_alpha())
    with update(credential):
        if credential.credential_type != "Amazon":
            credential.username = updated_value
        else:
            credential.access_key = updated_value
    view = navigate_to(credential, "Details")

    def wait_for_changes(field_name):
        wait_for(
            lambda: view.credential_options.get_text_of(field_name) == updated_value,
            fail_func=view.browser.selenium.refresh,
            delay=10,
            timeout=60
        )

    if credential.credential_type == "Amazon":
        wait_for_changes("Access Key")
    else:
        wait_for_changes("Username")
    credential.delete()


@pytest.mark.meta(blockers=[1437108])
@pytest.mark.tier(2)
def test_embed_tower_playbooks_list_changed(appliance, wait_for_ansible):
    "Tests if playbooks list changed after playbooks repo removing"
    playbooks = []
    repositories_collection = appliance.collections.ansible_repositories
    for repo_url in REPOSITORIES:
        repository = repositories_collection.create(
            fauxfactory.gen_alpha(),
            repo_url,
            description=fauxfactory.gen_alpha()
        )
        playbooks.append(set(playbook.name for playbook in repository.playbooks.all()))
        repository.delete()
    assert not set(playbooks[1]).issuperset(set(playbooks[0]))


@pytest.mark.tier(2)
def test_control_crud_ansible_playbook_action(request, catalog_item, action_collection):
    action = action_collection.create(
        fauxfactory.gen_alphanumeric(),
        action_type="Run Ansible Playbook",
        action_values={
            "run_ansible_playbook":
            {
                "playbook_catalog_item": catalog_item.name,
                "inventory": {
                    "target_machine": True
                }
            }
        }
    )
    request.addfinalizer(action.delete_if_exists)
    with update(action):
        ipaddr = fauxfactory.gen_ipaddr()
        new_descr = "edited_{}".format(fauxfactory.gen_alphanumeric())
        action.description = new_descr
        action.run_ansible_playbook = {
            "inventory": {
                "specific_hosts": True,
                "hosts": ipaddr
            }
        }
    view = navigate_to(action, "Edit")
    assert view.description.value == new_descr
    assert view.run_ansible_playbook.inventory.hosts.value == ipaddr
    action.delete()


@pytest.mark.tier(2)
def test_control_add_ansible_playbook_action_invalid_address(request, catalog_item,
        action_collection):
    action = action_collection.create(
        fauxfactory.gen_alphanumeric(),
        action_type="Run Ansible Playbook",
        action_values={
            "run_ansible_playbook":
            {
                "playbook_catalog_item": catalog_item.name,
                "inventory": {
                    "specific_hosts": True,
                    "hosts": "invalid_address_!@#$%^&*"
                }
            }
        }
    )
    request.addfinalizer(action.delete_if_exists)
    assert action.exists
    view = navigate_to(action, "Edit")
    assert view.run_ansible_playbook.inventory.hosts.value == "invalid_address_!@#$%^&*"


@pytest.mark.tier(2)
def test_embedded_ansible_credential_with_private_key(request, wait_for_ansible,
        credentials_collection):
    """Automation for BZ https://bugzilla.redhat.com/show_bug.cgi?id=1439589

    Adding new ssh credentials via Automation/Ansible/Credentials, add new credentials does not
    actually create new credentials with ssh keys.
    """
    private_key = """
-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA6J0DNbInTt35zDDq8obCRUH1uJvqoNEP+yEEHm/C1ipIC7vW
7ROuQMcPpsTgIVWBmFCOAt3TuQASYqo0mQrRHRPFDD0msPMMLcWENJ+4HkPZaZZX
k38HNuxa9NqPi5x/v008g4bER9OrleA2v5QPJHhcfLAjdL104gGeAK0G7+xJoJDA
NishuOkGC/qVBCaQ6qrEBlVHq6v5eSgXSJz3Jdd6GBdHy2xfokYHIEAb+qt3mW0G
ijXPBXDtBVguQ2OIJgEzmMh7sAjnAqogrH8FGRJUB+y7dqhZfmJSmSImoFo9/Akk
Ei+QbgijymVDmCLL16u7I9q6tHOVuhf9e3I0aQIDAQABAoIBAAuzwnKUGNgl4Kg+
GcPDtchILjVwWphmjBhFK/DgDHw7uk4k0AYzREPr/8STCPeEVrWz78EDKeCXuVUP
XQAKBEUjNnmMJgMm5wjyc9k148xZ+3kNYDCCZnmD4HuK90e9wst79jxjrkIyyuIK
Wpa+uxhJmdWIAvCfi17HWAyOp9ewAeKJJ8T2PwT56UyQi3DaR3YHALGra8Z676yT
NHQ/is6TE92GnRictgrmahYO7qke8h39NzHhH6/21PwSeSv3VDw3nKmz+9qY6sZU
GXCCu+ngPdCCXtBWDRewEBO2MMJb2LJwELR6PhXsPHN29vpZtfd8VudSZLYSOuik
0/cSPVUCgYEA9WtKNDJB0Xg0vNRpAiv6DKPovERrcTTwPrWaQI9WWOtxgFKGniAo
p84atPsxXqnpojAp6XAF3o33uf2S0L3pH2afNck5DLNy0AA0Bc0LGIFwzEqfbTGr
c1cpU1jV4N/x6Icbx+NdqfKcgH74t63vZb+4UixSOMnbi5oiM6Qu3DcCgYEA8qRi
ZfWfjQhWn4XZKcIRAWsjV8I3D7JjWyQ7r8mo1h/aLZe3cAmyi9VgRtltuQpwrLrD
1kgNL/l9oRALJoN/hKhpTKzNWiHf0zSNknp/xWlDmik3ToZ0SSwuETR5lNSgT//a
3oJLN8PXaoUBXDcsJy9McK4iZmS8dQ270SW/ZF8CgYEA5xOlY64qcNu49E8frG7R
2tL+UT4u2AHbb4A4hC8yQzk0vnl1zS9EeHPEi8G0g4iCtjaZT/YtYJbVuOb8NNWL
yggrQk58C+xu31BBq3Cb0PAX0BM3N248G7bm71ZG05yovqNwUe5QA7OvDgH/l5sL
PQeeuqiGpnfR4wk2yN7/TFMCgYAXYWWl43wjT9lg97nMP0n6NAOs0icSGSNfxecG
ck0VjO4uFH91iUmuFbp4OT1MZkgjLL/wJvM2WzkSywP4CxW/h6bV35TOCZOSu26k
3a7wK8t60Fvm8ifEYUBzIfZRNAfajZHefPmYfwOD3RsbcqmLgRBBj1X7Pdu2/8LI
TXXaywKBgQCaXeEZ5BTuD7FvMSX95EamDJ/DMyE8TONwDHMIowf2IQbf0Y5U7ntK
6pm5O95cJ7l2m3jUbKIUy0Y8HPW2MgwstcZXKkzlR/IOoSVgdiAnPjVKlIUvVBUx
0u7GxCs5nfyEPjEHTBn1g7Z6U8c6x1r7F50WsLzJftLfqo7tElNO5A==
-----END RSA PRIVATE KEY-----
"""
    credential = credentials_collection.create(
        fauxfactory.gen_alpha(),
        "Machine",
        username=fauxfactory.gen_alpha(),
        password=fauxfactory.gen_alpha(),
        private_key=private_key
    )
    request.addfinalizer(credential.delete)
    assert credential.exists
