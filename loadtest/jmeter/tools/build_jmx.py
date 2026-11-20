#!/usr/bin/env python3
"""Sinh greenmart-loadtest.jmx (JMeter 5.6). Chạy:  python3 tools/build_jmx.py > greenmart-loadtest.jmx
Giữ ở dạng script để dễ chỉnh luồng; có thể mở/sửa .jmx trực tiếp bằng JMeter GUI."""
from itertools import count
from xml.sax.saxutils import escape

_ids = count(1000)


def sp(name, value):   return f'<stringProp name="{name}">{escape(str(value))}</stringProp>'
def bp(name, value):   return f'<boolProp name="{name}">{"true" if value else "false"}</boolProp>'
def ip(name, value):   return f'<intProp name="{name}">{value}</intProp>'


def node(open_tag, children=()):
    """Một phần tử JMX + hashTree chứa các phần tử con (đúng cấu trúc JMeter)."""
    inner = "\n".join(children)
    return f"{open_tag}\n<hashTree>\n{inner}\n</hashTree>" if children else f"{open_tag}\n<hashTree/>"


def arguments_collection(pairs, http=False):
    out = []
    for n, v in pairs:
        if http:
            out.append(f'<elementProp name="{escape(n)}" elementType="HTTPArgument">{bp("HTTPArgument.always_encode", True)}'
                       f'{sp("Argument.value", v)}{sp("Argument.metadata", "=")}{bp("HTTPArgument.use_equals", True)}{sp("Argument.name", n)}</elementProp>')
        else:
            out.append(f'<elementProp name="{escape(n)}" elementType="Argument">{sp("Argument.name", n)}{sp("Argument.value", v)}{sp("Argument.metadata", "=")}</elementProp>')
    return "".join(out)


# ---------------------------------------------------------------- phần tử cấu hình
def http_defaults():
    return node('<ConfigTestElement guiclass="HttpDefaultsGui" testclass="ConfigTestElement" testname="HTTP Request Defaults" enabled="true">'
                '<elementProp name="HTTPsampler.Arguments" elementType="Arguments" guiclass="HTTPArgumentsPanel" testclass="Arguments" testname="User Defined Variables" enabled="true">'
                '<collectionProp name="Arguments.arguments"/></elementProp>'
                + sp("HTTPSampler.domain", "${__P(host,localhost)}") + sp("HTTPSampler.port", "${__P(port,80)}")
                + sp("HTTPSampler.protocol", "${__P(protocol,http)}") + sp("HTTPSampler.contentEncoding", "UTF-8")
                + sp("HTTPSampler.path", "") + sp("HTTPSampler.connect_timeout", "5000") + sp("HTTPSampler.response_timeout", "30000")
                + "</ConfigTestElement>")


def header_manager():
    hdrs = [("User-Agent", "GreenMart-LoadTest (Apache-JMeter)"), ("Accept", "text/html,application/xhtml+xml,*/*;q=0.8"),
            ("Accept-Language", "vi-VN,vi;q=0.9,en;q=0.8"), ("Accept-Encoding", "gzip, deflate"),
            # Django kiểm tra Origin/Referer cho request POST qua HTTPS -> luôn gửi
            ("Referer", "${base_url}/"), ("Origin", "${base_url}")]
    items = "".join(f'<elementProp name="" elementType="Header">{sp("Header.name", n)}{sp("Header.value", v)}</elementProp>' for n, v in hdrs)
    return node('<HeaderManager guiclass="HeaderPanel" testclass="HeaderManager" testname="HTTP Header Manager" enabled="true">'
                f'<collectionProp name="HeaderManager.headers">{items}</collectionProp></HeaderManager>')


def cookie_manager():
    return node('<CookieManager guiclass="CookiePanel" testclass="CookieManager" testname="HTTP Cookie Manager" enabled="true">'
                '<collectionProp name="CookieManager.cookies"/>' + bp("CookieManager.clearEachIteration", True)
                + bp("CookieManager.controlledByThreadGroup", False) + "</CookieManager>")


def csv_users():
    return node('<CSVDataSet guiclass="TestBeanGUI" testclass="CSVDataSet" testname="Users (username,password)" enabled="true">'
                + sp("delimiter", ",") + sp("fileEncoding", "UTF-8") + sp("filename", "${__P(users_csv,users.csv)}")
                + bp("ignoreFirstLine", True) + bp("quotedData", False) + bp("recycle", True) + sp("shareMode", "shareMode.all")
                + bp("stopThread", False) + sp("variableNames", "username,password") + "</CSVDataSet>")


# ---------------------------------------------------------------- sampler & thành phần con
def regex(name, refname, pattern, match_no, default):
    return node(f'<RegexExtractor guiclass="RegexExtractorGui" testclass="RegexExtractor" testname="{escape(name)}" enabled="true">'
                + sp("RegexExtractor.useHeaders", "false") + sp("RegexExtractor.refname", refname) + sp("RegexExtractor.regex", pattern)
                + sp("RegexExtractor.template", "$1$") + sp("RegexExtractor.default", default) + sp("RegexExtractor.match_number", match_no)
                + "</RegexExtractor>")


def assertion(name, field, test_type, strings):
    """field: Assertion.response_code | Assertion.response_data ; test_type: 8=Equals, 16=Substring."""
    ts = "".join(sp(str(next(_ids)), s) for s in strings)
    return node(f'<ResponseAssertion guiclass="AssertionGui" testclass="ResponseAssertion" testname="{escape(name)}" enabled="true">'
                f'<collectionProp name="Asserion.test_strings">{ts}</collectionProp>' + sp("Assertion.custom_message", "")
                + sp("Assertion.test_field", field) + bp("Assertion.assume_success", False) + ip("Assertion.test_type", test_type)
                + "</ResponseAssertion>")


def status_200():            return assertion("Status 200", "Assertion.response_code", 8, ["200"])
def contains(text, name=None): return assertion(name or f"Chứa '{text}'", "Assertion.response_data", 16, [text])


def sampler(name, path, method="GET", params=(), children=()):
    return node(f'<HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" testname="{escape(name)}" enabled="true">'
                '<elementProp name="HTTPsampler.Arguments" elementType="Arguments" guiclass="HTTPArgumentsPanel" testclass="Arguments" testname="User Defined Variables" enabled="true">'
                f'<collectionProp name="Arguments.arguments">{arguments_collection(params, http=True)}</collectionProp></elementProp>'
                + sp("HTTPSampler.domain", "") + sp("HTTPSampler.port", "") + sp("HTTPSampler.protocol", "") + sp("HTTPSampler.contentEncoding", "")
                + sp("HTTPSampler.path", path) + sp("HTTPSampler.method", method) + bp("HTTPSampler.follow_redirects", True)
                + bp("HTTPSampler.auto_redirects", False) + bp("HTTPSampler.use_keepalive", True) + bp("HTTPSampler.DO_MULTIPART_POST", False)
                + sp("HTTPSampler.embedded_url_re", "") + sp("HTTPSampler.connect_timeout", "") + sp("HTTPSampler.response_timeout", "")
                + "</HTTPSamplerProxy>", children)


def think_timer():
    return node('<UniformRandomTimer guiclass="UniformRandomTimerGui" testclass="UniformRandomTimer" testname="Think time (giữa các request)" enabled="true">'
                + sp("ConstantTimer.delay", "${__P(think_ms,1000)}") + sp("RandomTimer.range", "${__P(think_range_ms,2000)}") + "</UniformRandomTimer>")


def duration_assertion():
    return node('<DurationAssertion guiclass="DurationAssertionGui" testclass="DurationAssertion" testname="Chậm hơn max_ms thì tính là lỗi (SLA)" enabled="true">'
                + sp("DurationAssertion.duration", "${__P(max_ms,3000)}") + "</DurationAssertion>")


def if_controller(name, cond, children):
    return node(f'<IfController guiclass="IfControllerPanel" testclass="IfController" testname="{escape(name)}" enabled="true">'
                + sp("IfController.condition", cond) + bp("IfController.evaluateAll", False) + bp("IfController.useExpression", True)
                + "</IfController>", children)


def thread_group(name, threads_prop, default_threads, children):
    return node(f'<ThreadGroup guiclass="ThreadGroupGui" testclass="ThreadGroup" testname="{escape(name)}" enabled="true">'
                + sp("ThreadGroup.on_sample_error", "continue")
                + '<elementProp name="ThreadGroup.main_controller" elementType="LoopController" guiclass="LoopControlPanel" testclass="LoopController" testname="Loop Controller" enabled="true">'
                + bp("LoopController.continue_forever", False) + ip("LoopController.loops", -1) + "</elementProp>"
                + sp("ThreadGroup.num_threads", f"${{__P({threads_prop},{default_threads})}}") + sp("ThreadGroup.ramp_time", "${__P(rampup,60)}")
                + bp("ThreadGroup.scheduler", True) + sp("ThreadGroup.duration", "${__P(duration,300)}") + sp("ThreadGroup.delay", "")
                + bp("ThreadGroup.same_user_on_next_iteration", True) + "</ThreadGroup>", children)


CSRF = regex("Lấy CSRF token", "csrf", r'name="csrfmiddlewaretoken" value="([^"]+)"', "1", "CSRF_NOT_FOUND")
SLUG = regex("Lấy slug sản phẩm (ngẫu nhiên)", "slug", r"/detail/([\w-]+)", "0", "KHONG_CO_SAN_PHAM")


def guest_flow():
    return [
        sampler("G01 GET Trang chủ", "/", children=[status_200(), contains("Green Grocer", "Chứa tiêu đề trang chủ")]),
        sampler("G02 GET Danh sách sản phẩm", "/productPage/", children=[status_200(), contains("Thêm vào giỏ"), SLUG, CSRF]),
        sampler("G03 GET Chi tiết sản phẩm", "/detail/${slug}", children=[status_200(), contains("Đánh giá")]),
        sampler("G04 GET Tìm kiếm", "/search", params=[("q", "${__chooseRandom(rau,nho,chanh,khoai,táo,cải,kw)}")], children=[status_200()]),
        sampler("G05 POST Thêm vào giỏ", "/add-to-cart/${slug}", method="POST", params=[("csrfmiddlewaretoken", "${csrf}")], children=[status_200()]),
        sampler("G06 GET Giỏ hàng", "/cart/", children=[status_200(), contains("Giỏ hàng của bạn")]),
    ]


def user_flow():
    return [
        sampler("U01 GET Trang đăng nhập", "/loginn/", children=[status_200(), CSRF]),
        sampler("U02 POST Đăng nhập", "/loginn/", method="POST",
                params=[("fnm", "${username}"), ("password", "${password}"), ("csrfmiddlewaretoken", "${csrf}")],
                children=[status_200(), contains("Xin chào, ${username}", "Đăng nhập thành công (thấy 'Xin chào, user')")]),
        sampler("U03 GET Danh sách sản phẩm", "/productPage/", children=[status_200(), SLUG, CSRF]),
        sampler("U04 POST Thêm vào giỏ", "/add-to-cart/${slug}", method="POST", params=[("csrfmiddlewaretoken", "${csrf}")], children=[status_200()]),
        sampler("U05 GET Giỏ hàng", "/cart/", children=[status_200(), contains("Giỏ hàng của bạn")]),
        sampler("U06 GET Thanh toán", "/checkout/", children=[status_200(), contains("Thanh toán")]),
        sampler("U07 GET Hồ sơ cá nhân", "/profile/", children=[status_200()]),
        if_controller("Chỉ khi do_checkout=true (TẠO ĐƠN HÀNG THẬT)", "${__P(do_checkout,false)}",
                      [sampler("U08 GET Hoàn tất đơn hàng", "/complete/", children=[status_200()])]),
        sampler("U09 GET Đăng xuất", "/logout/", children=[status_200()]),
    ]


def build():
    udv = ("<elementProp name=\"TestPlan.user_defined_variables\" elementType=\"Arguments\" guiclass=\"ArgumentsPanel\" testclass=\"Arguments\" testname=\"User Defined Variables\" enabled=\"true\">"
           f'<collectionProp name="Arguments.arguments">{arguments_collection([("base_url", "${__P(base_url,http://localhost)}")])}</collectionProp></elementProp>')
    plan_open = ('<TestPlan guiclass="TestPlanGui" testclass="TestPlan" testname="GreenMart Load Test" enabled="true">'
                 + sp("TestPlan.comments", "Khách duyệt web + người dùng đăng nhập. Tham số qua -J (xem README).")
                 + bp("TestPlan.functional_mode", False) + bp("TestPlan.tearDown_on_shutdown", True) + bp("TestPlan.serialize_threadgroups", False)
                 + udv + sp("TestPlan.user_define_classpath", "") + "</TestPlan>")
    guest_tg = thread_group("TG1 Khách duyệt web", "guest_threads", 30, [think_timer(), duration_assertion()] + guest_flow())
    user_tg = thread_group("TG2 Người dùng đăng nhập + giỏ hàng", "user_threads", 10, [csv_users(), think_timer(), duration_assertion()] + user_flow())
    plan = node(plan_open, [http_defaults(), header_manager(), cookie_manager(), guest_tg, user_tg])
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6.3">\n<hashTree>\n' + plan + "\n</hashTree>\n</jmeterTestPlan>\n")


if __name__ == "__main__":
    print(build(), end="")
