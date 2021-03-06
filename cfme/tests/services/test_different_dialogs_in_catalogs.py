from datetime import date

import fauxfactory
import pytest
from widgetastic.exceptions import NoSuchElementException
from widgetastic.utils import partial_match

from cfme import test_requirements
from cfme.base.credential import Credential
from cfme.infrastructure.provider import InfraProvider
from cfme.markers.env_markers.provider import ONE_PER_TYPE
from cfme.services.service_catalogs import ServiceCatalogs
from cfme.utils.appliance.implementations.ui import navigate_to
from cfme.utils.generators import random_vm_name
from cfme.utils.log import logger

pytestmark = [
    pytest.mark.ignore_stream("upstream"),
    test_requirements.dialog,
    pytest.mark.long_running,
]

WIDGETS = {
    "Text Box": ("input", fauxfactory.gen_alpha(), "default_text_box"),
    "Check Box": ("checkbox", True, "default_value"),
    "Text Area": ("input", fauxfactory.gen_alpha(), "default_text_box"),
    "Radio Button": ("radiogroup", "One", "default_value_dropdown"),
    "Dropdown": ("dropdown", "Three", "default_value_dropdown"),
    "Tag Control": ("dropdown", "Service Level", "field_category"),
    "Timepicker": ("input", date.today().strftime("%m/%d/%Y"), None)
}


@pytest.fixture(scope="function")
def service_dialog(appliance, widget_name):
    service_dialog = appliance.collections.service_dialogs
    element_data = {
        'element_information': {
            'ele_label': fauxfactory.gen_alphanumeric(start="label_"),
            'ele_name': fauxfactory.gen_alphanumeric(start="name_"),
            'ele_desc': fauxfactory.gen_alphanumeric(start="desc_"),
            'choose_type': widget_name,
        },
        'options': {
            'field_required': True
        }
    }
    wt, value, field = WIDGETS[widget_name]
    if widget_name != "Timepicker":
        element_data['options'][field] = value

    sd = service_dialog.create(label=fauxfactory.gen_alphanumeric(start="dialog_"),
                               description="my dialog")
    tab = sd.tabs.create(tab_label=fauxfactory.gen_alphanumeric(start="tab_"),
                         tab_desc="my tab desc")
    box = tab.boxes.create(box_label=fauxfactory.gen_alphanumeric(start="box_"),
                           box_desc="my box desc")
    box.elements.create(element_data=[element_data])
    yield sd, element_data
    sd.delete_if_exists()


@pytest.fixture(scope="function")
def catalog_item(appliance, provider, provisioning, service_dialog, catalog):
    sd, element_data = service_dialog
    template, host, datastore, iso_file, vlan = list(map(
        provisioning.get, ('template', 'host', 'datastore', 'iso_file', 'vlan'))
    )
    provisioning_data = {
        'catalog': {'catalog_name': {'name': template, 'provider': provider.name},
                    'vm_name': random_vm_name('service')},
        'environment': {'host_name': {'name': host},
                        'datastore_name': {'name': datastore}},
        'network': {'vlan': partial_match(vlan)},
    }

    if provider.type == 'rhevm':
        provisioning_data['catalog']['provision_type'] = 'Native Clone'
    elif provider.type == 'virtualcenter':
        provisioning_data['catalog']['provision_type'] = 'VMware'

    catalog_item = appliance.collections.catalog_items.create(
        provider.catalog_item_type,
        name=fauxfactory.gen_alphanumeric(15, start="cat_item_"),
        description="my catalog",
        display_in=True,
        catalog=catalog,
        dialog=sd,
        prov_data=provisioning_data)
    yield catalog_item
    catalog_item.delete_if_exists()


@pytest.fixture(scope="function")
def generic_catalog_item(appliance, service_dialog, catalog):
    sd, element_data = service_dialog
    item_name = fauxfactory.gen_alphanumeric(15, start="cat_item_")
    catalog_item = appliance.collections.catalog_items.create(
        appliance.collections.catalog_items.GENERIC,
        name=item_name,
        description=fauxfactory.gen_alphanumeric(),
        display_in=True,
        catalog=catalog,
        dialog=sd)
    yield catalog_item
    catalog_item.delete_if_exists()


@pytest.mark.tier(2)
@pytest.mark.provider(
    [InfraProvider],
    selector=ONE_PER_TYPE,
    required_fields=[
        ["provisioning", "template"],
        ["provisioning", "host"],
        ["provisioning", "datastore"],
    ],
    scope="module",
)
@pytest.mark.parametrize("widget_name", ["Tag Control"], ids=["tag_control"])
def test_tagdialog_catalog_item(appliance, setup_provider, provider, catalog_item, request,
                                service_dialog, widget_name):
    """Tests tag dialog catalog item

    Bugzilla:
        1633540

    Metadata:
        test_flag: provision

    Polarion:
        assignee: nansari
        initialEstimate: 1/4h
        casecomponent: Services
        tags: service
    """
    sd, element_data = service_dialog
    vm_name = catalog_item.prov_data['catalog']["vm_name"]
    request.addfinalizer(
        lambda: appliance.collections.infra_vms.instantiate(
            f"{vm_name}0001", provider).cleanup_on_provider()
    )
    dialog_values = {element_data['element_information']['ele_name']: 'Gold'}
    service_catalogs = ServiceCatalogs(appliance, catalog=catalog_item.catalog,
                                       name=catalog_item.name, dialog_values=dialog_values)
    service_catalogs.order()
    logger.info(f'Waiting for cfme provision request for service {catalog_item.name}')
    provision_request = appliance.collections.requests.instantiate(catalog_item.name,
                                                                   partial_check=True)
    provision_request.wait_for_request()
    msg = f"Request failed with the message {provision_request.rest.message}"
    assert provision_request.is_succeeded(), msg


@pytest.fixture
def new_user(appliance, permission):
    # Tenant created
    collection = appliance.collections.tenants
    tenant = collection.create(
        name=fauxfactory.gen_alphanumeric(start="tenant_"),
        description=fauxfactory.gen_alphanumeric(),
        parent=collection.get_root_tenant(),
    )

    # Role created
    role = appliance.collections.roles.create(name=fauxfactory.gen_alphanumeric(start="role_"),
                                              vm_restriction="Only User or Group Owned",
                                              product_features=permission)
    # Group creates
    group = appliance.collections.groups.create(
        description=fauxfactory.gen_alphanumeric(start="grp_"),
        role=role.name,
        tenant=f"My Company/{tenant.name}"
    )
    creds = Credential(principal=fauxfactory.gen_alphanumeric(4),
                       secret=fauxfactory.gen_alphanumeric(4))
    # User created
    user = appliance.collections.users.create(name=fauxfactory.gen_alphanumeric(start="user_"),
                                              credential=creds, email=fauxfactory.gen_email(),
                                              groups=group, cost_center='Workload',
                                              value_assign='Database')
    yield user
    user.delete_if_exists()
    group.delete_if_exists()
    role.delete_if_exists()
    tenant.delete_if_exists()


@pytest.mark.meta(automates=[1576129])
@pytest.mark.tier(1)
@pytest.mark.customer_scenario
@pytest.mark.parametrize("permission", [[(['Everything'], True)],
                                        [(['Everything', 'Services', 'Requests'], False)]],
                         ids=['restricted', 'non-restricted'])
def test_should_be_able_to_access_services_requests_as_user(appliance, new_user, permission):
    """
    Bugzilla:
        1576129

    Polarion:
        assignee: nansari
        casecomponent: Services
        testtype: functional
        initialEstimate: 1/4h
        startsin: 5.9
        tags: service
    """
    with new_user:
        if permission == [(['Everything'], True)]:
            navigate_to(appliance.collections.requests, "All")
        else:
            with pytest.raises(NoSuchElementException):
                navigate_to(appliance.collections.requests, "All")


@pytest.mark.tier(1)
@pytest.mark.customer_scenario
@pytest.mark.meta(automates=[1385898])
@pytest.mark.parametrize(
    "widget_name", list(WIDGETS.keys()),
    ids=["_".join(w.split()).lower() for w in WIDGETS.keys()],
)
def test_dialog_elements_should_display_default_value(appliance, generic_catalog_item,
                                                      service_dialog, widget_name):
    """
    Bugzilla:
        1385898

    Polarion:
        assignee: nansari
        casecomponent: Services
        initialEstimate: 1/8h
        startsin: 5.10
        caseimportance: high
        testSteps:
            1. Create a dialog. Set default value
            2. Use the dialog in a catalog.
            3. Order catalog.
        expectedResults:
            1.
            2.
            3. Default values should be shown
    """
    sd, element_data = service_dialog
    service_catalogs = ServiceCatalogs(
        appliance, generic_catalog_item.catalog, generic_catalog_item.name
    )
    view = navigate_to(service_catalogs, "Order")
    ele_name = element_data["element_information"]["ele_name"]
    wt, value, field = WIDGETS[widget_name]
    get_attr = getattr(view.fields(ele_name), wt)
    if widget_name in ["Text Box", "Text Area", "Dropdown", "Timepicker", "Check Box"]:
        value = (date.today().strftime("%Y-%m-%d")
                 if widget_name == "Timepicker" and appliance.version < "5.11"
                 else value)
        assert get_attr.read() == value
    elif widget_name == "Tag Control":
        # In case of tag control, we do not have default value feature. But need to select category
        # to check whether it fetches respective values or not
        all_options = ["<Choose>", "Gold", "Platinum", "Silver"]

        for option in get_attr.all_options:
            assert option.text in all_options
    else:
        assert get_attr.selected == value
