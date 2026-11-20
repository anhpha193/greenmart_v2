import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE = __ENV.BASE_URL;

export const options = {
  stages: [
    { duration: '1m', target: 100 },
    { duration: '2m', target: 300 },
    { duration: '2m', target: 600 },
    { duration: '2m', target: 1000 },
    { duration: '2m', target: 0 },
  ],
  thresholds: {
    // dừng test nếu quá 1% request lỗi (sau 30 giây đầu)
    http_req_failed: [{ threshold: 'rate<0.01', abortOnFail: true, delayAbortEval: '30s' }],
    http_req_duration: ['p(95)<1500'],   // 95% request phải dưới 1,5 giây
  },
};

export default function () {
  // Người dùng chỉ xem (không ghi dữ liệu): trang chủ -> danh sách sản phẩm -> chi tiết -> khuyến mãi
  const home = http.get(`${BASE}/`);
  check(home, { 'home 200': (r) => r.status === 200 });
  sleep(1 + Math.random() * 2);

  const list = http.get(`${BASE}/product/`);
  check(list, { 'product 200': (r) => r.status === 200 });
  sleep(1 + Math.random() * 2);

  const re = /href="(\/?detail\/[^"]+)"/g;
  const links = [];
  let m;
  while ((m = re.exec(list.body || '')) !== null) links.push(m[1]);
  if (links.length > 0) {
    const p = links[Math.floor(Math.random() * links.length)];
    const d = http.get(`${BASE}${p.startsWith('/') ? p : '/' + p}`);
    check(d, { 'detail 200': (r) => r.status === 200 });
    sleep(1 + Math.random() * 2);
  }

  http.get(`${BASE}/promotions/`);
  sleep(1 + Math.random() * 2);
}