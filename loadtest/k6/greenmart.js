// Kịch bản k6 cho GreenMart: cùng luồng với JMeter (loadtest/jmeter), mỗi bài tối đa 5 phút.
//   k6 run -e BASE_URL=http://<dns-ALB> -e PROFILE=capacity greenmart.js      (hoặc dùng run-k6.sh)
//
// Hai nhóm người dùng (80% khách, 20% đăng nhập):
//   guest: trang chủ -> danh sách -> chi tiết -> tìm kiếm -> thêm vào giỏ -> giỏ hàng (mỗi lượt là khách mới)
//   user : đăng nhập 1 lần, rồi mỗi lượt: danh sách -> thêm vào giỏ -> giỏ hàng -> thanh toán -> hồ sơ -> (đặt hàng);
//          sau SESSION_ITERS lượt (mặc định 10) thì đăng xuất và đăng nhập lại
//
// PROFILE:
//   smoke     30 giây, 3 người: kiểm tra kịch bản, phải 0% lỗi
//   capacity  giữ đều RPS req/s (mặc định 250) trong 4 phút, đo p95/lỗi ở mức đó
//   ramp      tăng dần req/s từ 50 tới RPS_MAX (mặc định 500) trong 4,5 phút: tìm trần
//   stress    giống JMeter stress: tăng tới 350 người trong 3 phút rồi giữ 2 phút (so sánh với JMeter)
//   spike     dội từ 50 lên RPS req/s trong 10 giây, giữ 2 phút, hạ xuống
//
// Khác JMeter: capacity/ramp/spike dùng "arrival rate" (mô hình mở) - k6 tự thêm người để giữ đúng số
// lượt/giây kể cả khi server chậm, nên đo được trần thật thay vì bị máy test tự hãm lại.
//
// Biến môi trường: BASE_URL, PROFILE, RPS, RPS_MAX, DO_CHECKOUT=true (TẠO ĐƠN THẬT), USERS_CSV (mặc định ../jmeter/users.csv)

import http from 'k6/http';
import { check, group, sleep, fail } from 'k6';
import { SharedArray } from 'k6/data';
import exec from 'k6/execution';

const BASE = (__ENV.BASE_URL || '').replace(/\/$/, '');
if (!/^https?:\/\/[^/]+$/.test(BASE)) {
  throw new Error('Cần BASE_URL dạng http(s)://host[:port], vd. -e BASE_URL=http://<dns-ALB>');
}
const PROFILE = __ENV.PROFILE || 'smoke';
const RPS = Number(__ENV.RPS || 250);
const RPS_MAX = Number(__ENV.RPS_MAX || 500);
const DO_CHECKOUT = __ENV.DO_CHECKOUT === 'true';

// Mỗi lượt khách gửi 6 request, mỗi lượt user gửi 8-9 request (theo kịch bản bên dưới).
const GUEST_REQ = 6;
// User đăng nhập 1 lần rồi giữ session SESSION_ITERS lượt (như người dùng thật), nên mỗi lượt user trung bình
// khoảng 5-6 request (thêm đăng nhập/đăng xuất ở lượt đầu/cuối của phiên).
const SESSION_ITERS = Number(__ENV.SESSION_ITERS || 10);
const USER_REQ = DO_CHECKOUT ? 6 : 5;
// Đổi req/s mục tiêu thành số lượt/giây cho từng nhóm (80% request từ khách, 20% từ user).
function rates(rps) {
  return { guest: Math.max(1, Math.round((rps * 0.8) / GUEST_REQ)), user: Math.max(1, Math.round((rps * 0.2) / USER_REQ)) };
}

const users = new SharedArray('users', () => {
  const lines = open(__ENV.USERS_CSV || '../jmeter/users.csv').split(/\r?\n/).slice(1);
  return lines.filter((l) => l.trim()).map((l) => { const [u, p] = l.split(','); return { u: u.trim(), p: p.trim() }; });
});

// User: số VU CỐ ĐỊNH (tạo sẵn, đăng nhập một lần). Nếu để k6 tự thêm VU khi server chậm, mỗi VU mới đều phải
// đăng nhập -> "cơn bão đăng nhập" giả tạo làm sập phép đo. Hết VU rảnh thì k6 bỏ lượt (dropped_iterations).
const USER_VUS = Number(__ENV.USER_VUS || 150);
function arrival(exec, stages, startRate, maxVUs) {
  if (exec === 'user') maxVUs = USER_VUS;
  const pre = exec === 'user' ? maxVUs : Math.min(maxVUs, 100);
  return { executor: 'ramping-arrival-rate', exec, startRate, timeUnit: '1s', preAllocatedVUs: pre, maxVUs, stages };
}

function scenarios() {
  const r = rates(RPS);
  const rMax = rates(RPS_MAX);
  switch (PROFILE) {
    case 'smoke':
      return {
        guest: { executor: 'constant-vus', exec: 'guest', vus: 2, duration: '30s' },
        user: { executor: 'constant-vus', exec: 'user', vus: 1, duration: '30s' },
      };
    case 'capacity': // 30 giây tăng, 4 phút giữ, 15 giây hạ
      return {
        guest: arrival('guest', [{ target: r.guest, duration: '30s' }, { target: r.guest, duration: '4m' }, { target: 0, duration: '15s' }], 1, 800),
        user: arrival('user', [{ target: r.user, duration: '30s' }, { target: r.user, duration: '4m' }, { target: 0, duration: '15s' }], 1, 300),
      };
    case 'ramp': // 50 req/s -> RPS_MAX trong 4 phút 30 giây
      return {
        guest: arrival('guest', [{ target: rMax.guest, duration: '4m30s' }], rates(50).guest, 1200),
        user: arrival('user', [{ target: rMax.user, duration: '4m30s' }], rates(50).user, 400),
      };
    case 'stress': // như JMeter stress: 280 khách + 70 user, tăng 3 phút, giữ 2 phút
      return {
        guest: { executor: 'ramping-vus', exec: 'guest', startVUs: 0, stages: [{ target: 280, duration: '3m' }, { target: 280, duration: '2m' }] },
        user: { executor: 'ramping-vus', exec: 'user', startVUs: 0, stages: [{ target: 70, duration: '3m' }, { target: 70, duration: '2m' }] },
      };
    case 'spike':
      return {
        guest: arrival('guest', [{ target: rates(50).guest, duration: '30s' }, { target: r.guest, duration: '10s' }, { target: r.guest, duration: '2m' }, { target: 0, duration: '20s' }], 1, 1000),
        user: arrival('user', [{ target: rates(50).user, duration: '30s' }, { target: r.user, duration: '10s' }, { target: r.user, duration: '2m' }, { target: 0, duration: '20s' }], 1, 300),
      };
    default:
      throw new Error(`PROFILE không hợp lệ: ${PROFILE} (smoke|capacity|ramp|stress|spike)`);
  }
}

export const options = {
  scenarios: scenarios(),
  // Không theo dõi chuyển hướng như lỗi; POST thêm giỏ/đăng nhập trả 302 rồi k6 tự theo sang trang đích.
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<1500'],
    checks: ['rate>0.99'],
    // ramp: dừng sớm khi đã quá trần rõ ràng (lỗi > 10%), tránh dội tải vô ích
    ...(PROFILE === 'ramp' ? { 'http_req_failed{scenario:guest}': [{ threshold: 'rate<0.10', abortOnFail: true, delayAbortEval: '30s' }] } : {}),
  },
  summaryTrendStats: ['avg', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
  // Mặc định k6 xóa cookie sau mỗi lượt -> mất session đăng nhập. Giữ cookie; kịch bản khách tự xóa ở đầu lượt.
  noCookiesReset: true,
  discardResponseBodies: false,
  insecureSkipTLSVerify: false,
  userAgent: 'greenmart-k6/1.0',
};

const think = () => sleep(0.3 + Math.random() * 0.6); // như JMeter stress: 300-900 ms
const SEARCH = ['rau', 'nho', 'chanh', 'khoai', 'táo', 'cải', 'kw'];

function csrf(res) {
  const m = /name="csrfmiddlewaretoken" value="([^"]+)"/.exec(res.body || '');
  return m ? m[1] : null;
}

function randomSlug(res) {
  const slugs = [];
  const re = /\/detail\/([\w-]+)/g;
  let m;
  while ((m = re.exec(res.body || '')) !== null) slugs.push(m[1]);
  return slugs.length ? slugs[Math.floor(Math.random() * slugs.length)] : null;
}

function get(name, path, params = {}) {
  return http.get(`${BASE}${path}`, { tags: { name }, ...params });
}

// Referer = trang chủ (cùng origin): Django cần Referer khi chạy HTTPS, và web chuyển hướng về Referer sau khi
// thêm giỏ. KHÔNG dùng chính URL POST làm Referer: web sẽ chuyển hướng về /add-to-cart/... và k6 lặp vòng.
function post(name, path, form) {
  return http.post(`${BASE}${path}`, form, { tags: { name }, headers: { Referer: `${BASE}/` } });
}

// ---------- Khách duyệt web ----------
export function guest() {
  http.cookieJar().clear(BASE); // mỗi lượt là một khách mới (như JMeter clearEachIteration)
  let slug, token;

  group('G01 Trang chủ', () => {
    const r = get('G01 Trang chủ', '/');
    check(r, { 'G01 200': (x) => x.status === 200, 'G01 có tiêu đề': (x) => (x.body || '').includes('Green Grocer') });
  });
  think();
  group('G02 Danh sách sản phẩm', () => {
    const r = get('G02 Danh sách', '/productPage/');
    check(r, { 'G02 200': (x) => x.status === 200, 'G02 có nút thêm giỏ': (x) => (x.body || '').includes('Thêm vào giỏ') });
    slug = randomSlug(r);
    token = csrf(r);
  });
  if (!slug || !token) return; // trang lỗi: bỏ lượt này (đã tính lỗi ở check trên)
  think();
  group('G03 Chi tiết sản phẩm', () => {
    const r = get('G03 Chi tiết', `/detail/${slug}`);
    check(r, { 'G03 200': (x) => x.status === 200 });
  });
  think();
  group('G04 Tìm kiếm', () => {
    const r = get('G04 Tìm kiếm', `/search?q=${encodeURIComponent(SEARCH[Math.floor(Math.random() * SEARCH.length)])}`);
    check(r, { 'G04 200': (x) => x.status === 200 });
  });
  think();
  group('G05 Thêm vào giỏ', () => {
    const r = post('G05 Thêm vào giỏ', `/add-to-cart/${slug}`, { csrfmiddlewaretoken: token });
    check(r, { 'G05 200': (x) => x.status === 200 });
  });
  think();
  group('G06 Giỏ hàng', () => {
    const r = get('G06 Giỏ hàng', '/cart/');
    check(r, { 'G06 200': (x) => x.status === 200, 'G06 có giỏ': (x) => (x.body || '').includes('Giỏ hàng của bạn') });
  });
  think();
}

// ---------- Người dùng đăng nhập ----------
// Mỗi VU giữ trạng thái riêng giữa các lượt: đăng nhập ở lượt đầu, dùng session SESSION_ITERS lượt,
// rồi đăng xuất. Trước đây đăng nhập lại MỖI lượt -> băm mật khẩu (PBKDF2 1 triệu vòng) chiếm khoảng 30% CPU cluster,
// không giống người dùng thật (đăng nhập một lần cho cả buổi).
let session = null; // { acc, left } của VU này

function login() {
  const acc = users[(exec.vu.idInTest - 1) % users.length]; // mỗi VU một tài khoản
  http.cookieJar().clear(BASE);
  let ok = false;
  group('U01 Đăng nhập', () => {
    const page = get('U01 Trang đăng nhập', '/loginn/');
    const token = csrf(page);
    check(page, { 'U01 200': (x) => x.status === 200 });
    if (!token) return;
    const r = post('U02 POST Đăng nhập', '/loginn/', { fnm: acc.u, password: acc.p, csrfmiddlewaretoken: token });
    ok = check(r, { 'U02 đăng nhập được': (x) => x.status === 200 && (x.body || '').includes(`Xin chào, ${acc.u}`) });
  });
  session = ok ? { acc, left: SESSION_ITERS } : null;
  return ok;
}

export function user() {
  if (users.length === 0) fail('users.csv trống: chạy loadtest/jmeter/create_loadtest_users.py trước');
  if (!session && !login()) return;
  think();
  let token, slug;
  group('U03 Danh sách sản phẩm', () => {
    const r = get('U03 Danh sách', '/productPage/');
    check(r, { 'U03 200': (x) => x.status === 200 });
    slug = randomSlug(r);
    token = csrf(r);
  });
  if (!slug || !token) { session = null; return; } // mất session/trang lỗi: đăng nhập lại lượt sau
  think();
  group('U04 Thêm vào giỏ', () => {
    const r = post('U04 Thêm vào giỏ', `/add-to-cart/${slug}`, { csrfmiddlewaretoken: token });
    check(r, { 'U04 200': (x) => x.status === 200 });
  });
  think();
  group('U05 Giỏ hàng', () => {
    check(get('U05 Giỏ hàng', '/cart/'), { 'U05 200': (x) => x.status === 200 });
  });
  think();
  group('U06 Thanh toán', () => {
    const r = get('U06 Thanh toán', '/checkout/');
    check(r, { 'U06 200': (x) => x.status === 200, 'U06 có trang thanh toán': (x) => (x.body || '').includes('Thanh toán') });
  });
  think();
  group('U07 Hồ sơ', () => {
    check(get('U07 Hồ sơ', '/profile/'), { 'U07 200': (x) => x.status === 200 });
  });
  if (DO_CHECKOUT) {
    think();
    group('U08 Hoàn tất đơn hàng', () => {
      check(get('U08 Hoàn tất đơn', '/complete/'), { 'U08 200': (x) => x.status === 200 });
    });
  }
  session.left -= 1;
  if (session.left <= 0) { // hết phiên: đăng xuất, lượt sau đăng nhập lại
    think();
    group('U09 Đăng xuất', () => {
      check(get('U09 Đăng xuất', '/logout/'), { 'U09 200': (x) => x.status === 200 });
    });
    session = null;
  }
  think();
}
