import pytest

import app as app_module

REQUIRED_IDS = [
    "logBox", "spinnerBox", "toastBox", "selImage", "selFlavor",
    "btnSetup", "btnCreateLb", "btnScaleOut", "btnScaleIn", "selScaleIn",
    "tblFlavors", "tblImages", "tblNetworks", "tblSubnets", "tblRouters", "tblServers",
    "btnCreateVm", "btnCreateWebVm", "btnCreateNet", "btnCreateSubnet", "btnCreateRouter",
    "tblLoadBalancers", "inLbName", "btnReloadLb",
]


@pytest.fixture
def page():
    app_module.app.config["TESTING"] = True
    res = app_module.app.test_client().get("/")
    assert res.status_code == 200
    return res.get_data(as_text=True)


@pytest.mark.parametrize("element_id", REQUIRED_IDS)
def test_page_has_element(page, element_id):
    assert f'id="{element_id}"' in page


def test_page_has_title_and_single_h1(page):
    assert "<title>Nhóm 17 - NT533" in page
    assert page.count("<h1") == 1
    assert "Nhật ký hệ thống (Activity Logs)" in page
    assert "vui lòng đợi" in page
