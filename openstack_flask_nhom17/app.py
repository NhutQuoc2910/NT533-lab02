"""Flask server: giao diện (/) và REST endpoints nội bộ (/api/...) bọc OpenStackClient."""
from flask import Flask, jsonify, render_template, request

import config
from openstack_api import OpenStackClient, OpenStackError

app = Flask(__name__)
client = OpenStackClient()


class BadRequest(Exception):
    pass


def _body():
    return request.get_json(silent=True) or {}


def _require(body, *fields):
    missing = [f for f in fields if not body.get(f)]
    if missing:
        raise BadRequest("Thiếu trường bắt buộc: " + ", ".join(missing))


def _respond(success, message, logs=None, data=None, status=200):
    payload = {
        "success": success,
        "message": message,
        "logs": logs or [],
        "data": data if data is not None else {},
    }
    return jsonify(payload), status


def _handle(action, ok_message):
    """Chạy action(logs) và đóng gói kết quả thành envelope; logs luôn được giữ lại khi lỗi."""
    logs = []
    try:
        data = action(logs)
    except BadRequest as err:
        return _respond(False, str(err), logs, status=400)
    except OpenStackError as err:
        return _respond(False, err.message, logs)
    except Exception as err:  # noqa: BLE001 - trả lỗi bất ngờ về cho giao diện
        return _respond(False, f"Lỗi không lường trước: {err}", logs, status=500)
    return _respond(True, ok_message, logs, data)


@app.get("/")
def index():
    return render_template("index.html")


# ----- Yêu cầu 1 -----

@app.get("/api/flavors")
def api_flavors():
    return _handle(lambda logs: {"items": client.list_flavors()}, "Đã tải danh sách Flavor")


@app.get("/api/images")
def api_images():
    return _handle(lambda logs: {"items": client.list_images()}, "Đã tải danh sách Image")


# ----- Yêu cầu 2 -----

@app.get("/api/networks")
def api_networks():
    return _handle(lambda logs: {"items": client.list_networks()}, "Đã tải danh sách Network")


@app.post("/api/networks")
def api_create_network():
    def action(logs):
        body = _body()
        _require(body, "name")
        net = client.create_network(body["name"])
        logs.append(f"Đã tạo network {net.get('name', body['name'])} ({net.get('id', '')})")
        return {"item": net}

    return _handle(action, "Đã tạo network")


@app.delete("/api/networks/<network_id>")
def api_delete_network(network_id):
    def action(logs):
        client.delete_network(network_id)
        logs.append(f"Đã xóa network {network_id} cùng các subnet con")
        return {}

    return _handle(action, "Đã xóa network")


@app.get("/api/subnets")
def api_subnets():
    return _handle(lambda logs: {"items": client.list_subnets()}, "Đã tải danh sách Subnet")


@app.post("/api/subnets")
def api_create_subnet():
    def action(logs):
        body = _body()
        _require(body, "network_id", "name", "cidr")
        subnet = client.create_subnet(
            body["network_id"], body["name"], body["cidr"],
            gateway_ip=body.get("gateway_ip") or None,
        )
        logs.append(f"Đã tạo subnet {subnet.get('name', body['name'])} ({subnet.get('id', '')})")
        return {"item": subnet}

    return _handle(action, "Đã tạo subnet")


@app.delete("/api/subnets/<subnet_id>")
def api_delete_subnet(subnet_id):
    def action(logs):
        client.delete_subnet(subnet_id)
        logs.append(f"Đã xóa subnet {subnet_id}")
        return {}

    return _handle(action, "Đã xóa subnet")


# ----- Yêu cầu 3 -----

@app.get("/api/routers")
def api_routers():
    return _handle(lambda logs: {"items": client.list_routers()}, "Đã tải danh sách Router")


@app.post("/api/routers")
def api_create_router():
    def action(logs):
        body = _body()
        _require(body, "name")
        external_id = client.get_external_network_id() if body.get("external", True) else None
        router = client.create_router(body["name"], external_network_id=external_id)
        logs.append(f"Đã tạo router {router.get('name', body['name'])} ({router.get('id', '')})")
        return {"item": router}

    return _handle(action, "Đã tạo router")


@app.delete("/api/routers/<router_id>")
def api_delete_router(router_id):
    def action(logs):
        client.delete_router(router_id)
        logs.append(f"Đã gỡ interface và xóa router {router_id}")
        return {}

    return _handle(action, "Đã xóa router")


@app.post("/api/routers/<router_id>/interfaces")
def api_attach_interface(router_id):
    def action(logs):
        body = _body()
        _require(body, "subnet_id")
        client.add_router_interface(router_id, body["subnet_id"])
        logs.append(f"Đã gắn subnet {body['subnet_id']} vào router {router_id}")
        return {}

    return _handle(action, "Đã gắn interface")


@app.delete("/api/routers/<router_id>/interfaces")
def api_detach_interface(router_id):
    def action(logs):
        body = _body()
        _require(body, "subnet_id")
        client.remove_router_interface(router_id, subnet_id=body["subnet_id"])
        logs.append(f"Đã gỡ subnet {body['subnet_id']} khỏi router {router_id}")
        return {}

    return _handle(action, "Đã gỡ interface")


# ----- Yêu cầu 4, 6 -----

@app.get("/api/servers")
def api_servers():
    return _handle(lambda logs: {"items": client.list_servers()}, "Đã tải danh sách máy ảo")


def _create_vm_action(web):
    def action(logs):
        body = _body()
        _require(body, "image_id", "flavor_id")
        name = body.get("name") or client.next_vm_name()
        vm = client.create_vm(name, body["image_id"], body["flavor_id"], web=web, logs=logs)
        return {"item": vm}

    return action


@app.post("/api/servers")
def api_create_server():
    return _handle(_create_vm_action(web=False), "Đã tạo máy ảo")


@app.post("/api/servers/web")
def api_create_web_server():
    return _handle(_create_vm_action(web=True), "Đã tạo máy ảo Web Server (Nginx)")


@app.delete("/api/servers/<server_id>")
def api_delete_server(server_id):
    def action(logs):
        client.delete_server(server_id)
        logs.append(f"Đã gửi yêu cầu xóa máy ảo {server_id}")
        return {}

    return _handle(action, "Đã xóa máy ảo")


@app.post("/api/servers/<server_id>/floating-ip")
def api_floating_ip(server_id):
    return _handle(lambda logs: client.assign_floating_ip(server_id, logs), "Đã gán Floating IP")


# ----- Yêu cầu 5, 7, 8 -----

@app.post("/api/setup")
def api_setup():
    return _handle(lambda logs: client.setup_network_1click(logs), "Thiết lập mạng hoàn tất")


@app.post("/api/loadbalancer")
def api_loadbalancer():
    return _handle(lambda logs: client.create_load_balancer(logs), "Đã tạo Load Balancer")


@app.post("/api/scale-out")
def api_scale_out():
    return _handle(lambda logs: client.scale_out(logs), "Scale Out hoàn tất")


@app.post("/api/scale-in")
def api_scale_in():
    def action(logs):
        return client.scale_in(logs, server_id=_body().get("server_id") or None)

    return _handle(action, "Scale In hoàn tất")


if __name__ == "__main__":
    config.validate()
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
