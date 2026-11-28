import { initHeader } from './components/Header.js?202511139';
import { breakP } from './components/Utils.js?202511139';
import { setAriaLabelCategoryMoreLink } from './components/Accessibility.js?202511139';

/**
 * アンカーリンクのスムーズスクロール
 */
function anchorLinkSmoothScroll() {
  // Live配信ページは「直近のLIVE配信」がJSONで呼び出されてから遅延でスクロールさせるため使わない
  const currentUrl = window.location.pathname;
  if (currentUrl === '/live/index.html') return;

  const anchorElms = document.querySelectorAll('a[href^="#anchor"]');
  anchorElms.forEach(anchorElm => {
    anchorElm.addEventListener('click', function (e) {
      e.preventDefault();
      document.querySelector(this.getAttribute('href')).scrollIntoView({
        behavior: 'smooth',
      });
    });
  });
}

/**
 * プライバシーポリシーに関するCookieポップアップ
 */
function cookieAcceptPopup() {
  // spiralドメインでは除外
  const currentUrl = window.location.href;
  const isInErrorDomain = /https:\/\/reg31.smp.ne.jp\/regist\/Reg2/.test(currentUrl);
  if (isInErrorDomain) return;
  // error配下の時は表示させない
  const currentPage = window.location.pathname;
  const isInErrorDir = /^\/error\//.test(currentPage);
  if (isInErrorDir) return;

  const cookieStr = document.cookie;
  const BooleanCheck = cookieStr.indexOf('boatcast_cookie_accept', 0);

  if (BooleanCheck === -1) {
    const $cookiePopup = document.createElement('div');
    $cookiePopup.classList.add('cookiePopup');
    $cookiePopup.classList.add('js-cookiePopup');
    $cookiePopup.innerHTML = `
      <div class="cookiePopup__inner">
        <p class="txt">
          当ウェブサイトは、<br class="only-sp">サービスの利便性を高めるために、<br class="only-sp">Cookie（クッキー）を使用しています。<br>
          Cookieの 使用に関しては、<br class="only-sp">「<a href="/static/privacypolicy/index.html" class="cookiePopup__txtLink">プライバシーポリシー</a>」をお読みください。
        </p>
        <button class="cookiePopup__acceptBtn js-acceptBtn">同意する</button>
        <button class="cookiePopup__closeBtn js-closeBtn" aria-label="閉じる">
          <img class="cookiePopup__icon" src="/assets/img/icon_popup-close.svg" width="32" height="32" aria-hidden="true">
          閉じる
        </button>
      </div>`;
    document.body.appendChild($cookiePopup);
    $cookiePopup.querySelector('.js-acceptBtn').onclick = function () {
      const expireDate = new Date();
      // クッキー有効期限は1年
      expireDate.setTime(expireDate.getTime() + 365 * 24 * 60 * 60 * 1000);
      document.cookie =
        'cookie_policy=boatcast_cookie_accept; path=/; expires=' + expireDate.toUTCString();
      $cookiePopup.style.display = 'none';
    };
    $cookiePopup.querySelector('.js-closeBtn').onclick = function () {
      $cookiePopup.style.display = 'none';
    };
  }
}

/**
 * mainタグにあるpage-${id}へ動的にアンカーリンクを設置して
 * スキップリンクを実行
 */
function skipGotoMain() {
  const skipLink = document.querySelector('.js-skipGotoMain');
  const mainElm = document.querySelector('main');
  const pageID = mainElm.getAttribute('id');
  const headerHeight = document.querySelector('#header').offsetHeight;
  skipLink.setAttribute('href', `#${pageID}`);
  skipLink.addEventListener('click', e => {
    mainElm.scrollIntoView({
      behavior: 'smooth',
    });
  });
  window.scrollBy(0, -headerHeight);
}

/**
 * トップページかどうかのチェックして
 * グロナビとクイックメニューのアンカーリンクのパスを変更
 */
function loadedChangeAnchorPath() {
  const isTopPage = window.location.pathname === '/' || window.location.pathname === '/index.html';

  document.querySelectorAll('.js-changeAnchorPath').forEach(anchor => {
    anchor.addEventListener('click', function (e) {
      const hrefValue = anchor.getAttribute('href');
      if (isTopPage) {
        const anchorPart = hrefValue.split('#')[1];
        const target = document.querySelector('#' + anchorPart);
        if (target) {
          e.preventDefault();
          if (breakP._SP) {
            // ハンバーガーメニューを閉じる処理を直接ここに記述
            const hamburgerMenu = document.querySelector('.js-navList');
            if (hamburgerMenu && hamburgerMenu.classList.contains('is-visible')) {
              hamburgerMenu.classList.remove('is-visible');
              document.body.classList.remove('is-locked');
              const navButton = document.querySelector('.js-navButton');
              if (navButton) {
                navButton.setAttribute('aria-expanded', 'false');
              }
            }
          }
          target.scrollIntoView({
            behavior: 'smooth',
          });
        }
      } else {
        e.preventDefault();
        window.location = '/index.html' + hrefValue;
      }
    });
  });
}

$(function () {
  $('#header').load('/assets/include/common_header.html', function () {
    initHeader();
    skipGotoMain();
    loadedChangeAnchorPath();
  });
  $('#footer').load('/assets/include/common_footer.html', function () {
    setAriaLabelCategoryMoreLink();
    cookieAcceptPopup();
    anchorLinkSmoothScroll();
    loadedChangeAnchorPath();
  });
});
