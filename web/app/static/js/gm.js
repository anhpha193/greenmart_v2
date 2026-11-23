// Phần riêng của từng người trên trang công khai (trang chủ, danh sách, chi tiết, tìm kiếm).
// HTML của các trang này giống nhau cho mọi người (để nginx/CloudFront cache được); JS lấy phần riêng qua /api/me/:
//   - tên người dùng, nút đăng nhập/đăng xuất, form viết đánh giá
//   - số món trong giỏ
//   - thông báo (messages)
//   - CSRF token: chép từ cookie csrftoken vào các form (cookie do /api/me/ đặt)
// Nút "Thêm vào giỏ" gửi bằng fetch và nhận JSON: không tải lại trang, không có chuyển hướng 302.
(function () {
  'use strict';

  function cookie(name) {
    var m = document.cookie.match('(?:^|; )' + name + '=([^;]*)');
    return m ? decodeURIComponent(m[1]) : '';
  }

  function fillCsrf() {
    var token = cookie('csrftoken');
    document.querySelectorAll('input[data-gm-csrf]').forEach(function (i) { i.value = token; });
    return token;
  }

  function toastContainer() {
    var c = document.getElementById('toast-container');
    if (!c) {
      c = document.createElement('div');
      c.id = 'toast-container';
      c.className = 'fixed top-20 right-5 z-50 space-y-2';
      document.body.appendChild(c);
    }
    return c;
  }

  function toast(text) {
    var t = document.createElement('div');
    t.className = 'toast bg-green-600 text-white px-4 py-2 rounded-lg shadow-lg text-sm font-medium ' +
      'transition-opacity duration-1000 ease-in-out opacity-100';
    t.textContent = text;
    toastContainer().appendChild(t);
    setTimeout(function () {
      t.classList.add('opacity-0');
      setTimeout(function () { t.remove(); }, 1000);
    }, 3000);
  }

  function setCartCount(n) {
    document.querySelectorAll('[data-gm-cart-count]').forEach(function (e) {
      e.textContent = n;
      e.style.display = n > 0 ? 'flex' : 'none';
    });
  }

  function show(el, visible) {
    el.style.display = visible ? (el.getAttribute('data-gm-show') || '') : 'none';
  }

  function apply(me) {
    document.querySelectorAll('[data-gm-auth="in"]').forEach(function (e) { show(e, me.authenticated); });
    document.querySelectorAll('[data-gm-auth="out"]').forEach(function (e) { show(e, !me.authenticated); });
    document.querySelectorAll('[data-gm-username]').forEach(function (e) { e.textContent = me.username || ''; });
    setCartCount(me.cart_count || 0);
    (me.messages || []).forEach(toast);
    fillCsrf();
  }

  var ready = fetch('/api/me/', { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (me) { if (me) apply(me); return me; })
    .catch(function () { return null; });

  var ADD_TO_CART = /\/add-to-cart(-detail)?\//;

  document.addEventListener('submit', function (ev) {
    var form = ev.target;
    if ((form.method || '').toLowerCase() !== 'post') return;
    var isCart = ADD_TO_CART.test(form.action);
    if (!isCart && cookie('csrftoken')) { fillCsrf(); return; } // form thường: gửi như cũ, chỉ cần token

    ev.preventDefault();
    ready.then(function () {
      fillCsrf();
      if (!isCart) { form.submit(); return; } // chưa có cookie lúc bấm: đợi /api/me/ rồi gửi
      return fetch(form.action, {
        method: 'POST', body: new FormData(form), credentials: 'same-origin',
        headers: { 'X-Requested-With': 'XMLHttpRequest', Accept: 'application/json' },
      }).then(function (res) {
        var json = (res.headers.get('content-type') || '').indexOf('application/json') >= 0;
        if (!res.ok || !json) { form.submit(); return; } // lỗi: để server xử lý như form thường
        return res.json().then(function (d) {
          setCartCount(d.cart_count || 0);
          if (d.message) toast(d.message);
        });
      });
    }).catch(function () { form.submit(); });
  }, true);
})();
