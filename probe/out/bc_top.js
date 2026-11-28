import { CONFIG } from './config.js?202511139';
import {
  breakP,
  checkStatusDate,
  fetchAllData,
  sortDataByDateTime,
  movieTimeLengthDataFormatter,
  displayMessageForEmptyCardGroups,
  limitedViewCards,
} from './components/Utils.js?202511139';
import {
  setAriaSelected,
  setAriaHidden
} from './components/Accessibility.js?202511139';
import {
  getServerDateTime,
  formatDateTime,
  getWeekDatesFormatted
} from './components/DateTime.js?202511139';
import {
  createNewsArticleHTML
} from './components/NewsArticle.js?202511139';
import {
  generateRankingCards
} from '/assets/js/components/TodaysRankingGroup.js?202511139';
import {
  Card
} from '/assets/js/components/Card.js?202511139';
import {
  BoatCastNewsGroup
} from '/assets/js/components/BoatCastNewsGroup.js?202511139';

/**
 * MVカルーセルの初期化
 */
function initCarousel() {
  const $slider = $('.js-mainVisualCarousel');
  const $sliderButton = $('.js-mainVisualCarouselButton');
  // slick 初期化
  $slider.slick({
    dots: true,
    speed: 700,
    fade: true,
    autoplaySpeed: 7000,
    autoplay: true,
    prevArrow: '<button type="button" class="slick-prev" aria-label="前へ"></button>',
    nextArrow: '<button type="button" class="slick-next" aria-label="次へ"></button>',
    cssEase: 'linear',
  });

  // PCのみ再生ボタンの位置をページャーの幅から算出して感覚をとる
  const fixedSliderDots = () => {
    const $dots = $('.slick-dots li');
    const flexGapSize = 12;
    let resultWidth = 0;

    if (breakP._PC) {
      $dots.each(function (index, dot) {
        const width = $(dot).width();
        resultWidth += width + flexGapSize;
      });
      resultWidth += flexGapSize;

      const windowW = $(window).width();
      const contentW = $('.slick-dots').width();
      let oneMargin;
      // 1200px - SPの範囲の余白
      if (window.matchMedia('(max-width: 1200px)').matches) {
        oneMargin = 75 - 15;
      } else {
        oneMargin = (windowW - contentW) / 2 - 15;
      }
      $sliderButton.css('left', `${resultWidth + oneMargin}px`);
    }else{
      $sliderButton.css('left', '');
    }
  };
  fixedSliderDots();
  window.addEventListener('resize', fixedSliderDots);

  // 再生、停止ボタンの挙動
  $sliderButton.on('click', function () {
    if ($sliderButton.hasClass('is-start')) {
      $slider.slick('slickPause');
      $sliderButton.toggleClass('is-start is-pause');
    } else if ($sliderButton.hasClass('is-pause')) {
      $slider.slick('slickPlay');
      $sliderButton.toggleClass('is-start is-pause');
    }
  });
  // next, prev, スライドなどのイベントによるオートスライドの挙動
  $slider.on('afterChange', function () {
    $slider.slick('slickPlay');
  });
  // 読み込み時のカクツキの防止
  $slider.css('visibility', 'visible');
}

/**
 * MVカルーセルのアイテムのデータ属性からライブ配信のラベルを生成・スイッチ
 * @async
 */
async function populateMvSliderBodyWithLiveState() {
  const $sliderItems = $('.js-carouselSwitchState');
  if (!$sliderItems.length) return;

  const now = new Date((await getServerDateTime()).replace(/-/g, '/'));
  $sliderItems.each(function () {
    const $sliderItem = $(this);
    const videoData = extractVideoData($sliderItem);
    // console.log(videoData);

    if (shouldProcessVideo(videoData)) {
      const liveState = determineLiveState(videoData, now);
      const html = generateScheduleLabelHTML(liveState, videoData.title, videoData.liveTime);
      $sliderItem.find('.mainVisualCarousel__body').prepend(html);
    }
  });
}

/**
 * 指定の要素から動画のデータを抽出
 * @param {HTMLElement} $element - 動画データを抽出する要素
 * @returns {Object} 動画のデータ
 */
function extractVideoData($element) {
  const liveTime = $element.attr('data-video_live_time');
  return {
    title: $element.attr('data-video_title'),
    type: $element.attr('data-video_type'),
    streamStatus: $element.attr('data-video_stream_status'),
    isLive: $element.attr('data-video_isLive'),
    liveTime: liveTime ? new Date(liveTime.replace(/-/g, '/')) : null,
  };
}

/**
 * 動画データを処理するかどうかを判定
 * @param {Object} videoData - 動画のデータ
 * @returns {boolean} 処理を行う場合はtrue, それ以外はfalse
 */
function shouldProcessVideo(videoData) {
  return (
    videoData.type === '0' ||
    (videoData.type === '1' && videoData.isLive === '1') ||
    videoData.liveTime === null
  );
  // return true;
}

/**
 * 動画のライブ状態を判定
 * @param {Object} videoData - 動画のデータ
 * @param {Date} now - 現在の日時
 * @returns {string} ライブ状態
 */
function determineLiveState(videoData, now) {
  if (videoData.streamStatus === '0' && videoData.liveTime && videoData.liveTime <= now) {
    return 'isLiveNow';
  } else if (videoData.liveTime && isSameDay(videoData.liveTime, now) && videoData.liveTime > now) {
    return 'isTodayLive';
  } else if (videoData.liveTime && videoData.liveTime > now) {
    return 'isTomorrowLive';
  } else {
    return '';
  }
}

/**
 * 2つの日付が同じ日であるかどうかを判定
 * @param {Date} date1 - 判定する日付1
 * @param {Date} date2 - 判定する日付2
 * @returns {boolean} 同じ日であればtrue, それ以外はfalse
 */
function isSameDay(date1, date2) {
  return formatDateTime(date1, 'YYYY-MM-DD') === formatDateTime(date2, 'YYYY-MM-DD');
}

/**
 * ライブ状態に基づいてHTMLラベルを生成
 * @param {string} liveState - ライブ状態
 * @param {string} videoTitle - 動画のタイトル
 * @param {Date} videoLiveTime - ライブの開始時間
 * @returns {string} 生成されたHTMLラベル
 */
function generateScheduleLabelHTML(liveState, videoTitle, videoLiveTime) {
  const liveStateLabels = {};
  // console.log(videoLiveTime);
  if (videoLiveTime !== null) {
    liveStateLabels.isLiveNow = `<div class="timeSchedule is-liveNow">
      <time class="time" aria-label="${formatDateTime(
        videoLiveTime,
        'M月D日H時m分'
      )}" datetime="${formatDateTime(videoLiveTime, 'YYYY-MM-DD HH:mm')}">
      本日${formatDateTime(videoLiveTime, 'HH:mm')}～</time>
      <span class="label">LIVE
        <span class="liveAnime" aria-hidden="true">
          <span class="wave">）</span>
          <span class="wave">）</span>
          <span class="wave">）</span>
        </span>
      </span>
    </div>`;

    liveStateLabels.isTodayLive = `<div class="timeSchedule is-today">
      <time class="time" aria-label="${formatDateTime(
        videoLiveTime,
        'M月D日H時m分'
      )}" datetime="${formatDateTime(videoLiveTime, 'YYYY-MM-DD HH:mm')}">
        本日${formatDateTime(videoLiveTime, 'HH:mm')}～
      </time>
    </div>`;

    liveStateLabels.isTomorrowLive = `<div class="timeSchedule">
      <time class="time" aria-label="${formatDateTime(
        videoLiveTime,
        'M月D日W曜日H時m分'
      )}" datetime="${formatDateTime(videoLiveTime, 'YYYY-MM-DD HH:mm')}">
      ${formatDateTime(videoLiveTime, 'M/D(W)HH:mm')}
      </time>
    </div>`;
  }
  // console.log(liveStateLabels[liveState]);
  return `
    <p class="mainVisualCarousel__title">${videoTitle}</p>
    ${
  liveStateLabels[liveState]
        ? `<div class="mainVisualCarousel__status">${liveStateLabels[liveState]}</div>`
        : ''
}
  `;
}

/**
 * お知らせのHTMLを表示
 */
function showInfoHTML() {
  let disp_info_items = false;

  $('.js-noticeInfo .noticeInfo').each(function () {
    const $notification = $(this);
    const start = new Date($notification.data('notification_start').replace(/-/g, '/'));
    const end = new Date($notification.data('notification_end').replace(/-/g, '/'));
    const nowTime = new Date();

    if (nowTime < start || end < nowTime) {
      $notification.remove();
    } else {
      $notification.show().addClass('is-visible');
      disp_info_items = true;
    }
  });

  $('.js-noticeInfo').each(function () {
    const $notifications = $(this);

    if ($notifications.find('.noticeInfo').length === 0) {
      $notifications.remove();
    }
  });

  if (disp_info_items) {
    $('.js-noticeInfo').parent().show();
  }
}


/**
 * 配信スケジュールのHTMLを生成
 * @param {Object[]} contents - 配信スケジュールの内容
 *                              liveStartDateTime:開始時刻（LIVEアイコンの切り替えで仕様）
 *                              scheduledStartDateTime:予定時刻(本日h:m～といったテキスト表示)
 * @returns {string} 生成されたHTML
 */
function createTimeSchedulesHTML(contents) {
  const contentsWithState = contents.map(setContentState);

  return contentsWithState
    .map(content => {
      return Card({
        url: content.liveUrl || '',
        thumbnail: content.liveThumbnail || '',
        state: content.state || '',
        scheduledStartDateTime: content.scheduledStartDateTime || '',
        timeText: content.timeText || '',
        title: content.liveTitle || '',
        hasAssetsPath: true,
        channelName: content.displayChannelName || '',
      });
    })
    .join('');
}

/**
 * 配信スケジュールの状態をセット
 * @param {Object} content - コンテンツデータ
 * @returns {Object} 状態がセットされたコンテンツデータ
 */
function setContentState(content) {
  const currentDateTime = new Date();
  const scheduledStartDate = new Date(content.scheduledStartDateTime.replace(/-/g, '/'));
  const liveStartDate = new Date(content.liveStartDateTime.replace(/-/g, '/'));
  const liveEndDate = new Date(content.liveEndDateTime.replace(/-/g, '/'));

  // 日本時間に合わせた日付の文字列を生成
  const currentDateStr = `${currentDateTime.getFullYear()}-${String(
    currentDateTime.getMonth() + 1
  ).padStart(2, '0')}-${String(currentDateTime.getDate()).padStart(2, '0')}`;
  const scheduledStartDateStr = `${scheduledStartDate.getFullYear()}-${String(
    scheduledStartDate.getMonth() + 1
  ).padStart(2, '0')}-${String(scheduledStartDate.getDate()).padStart(2, '0')}`;
  const liveStartDateStr = `${liveStartDate.getFullYear()}-${String(
    liveStartDate.getMonth() + 1
  ).padStart(2, '0')}-${String(liveStartDate.getDate()).padStart(2, '0')}`;

  content.state = '';
  content.timeText = `${formatDateTime(content.scheduledStartDateTime, 'M/D HH:mm')}～`;

  // 本日中（もしくは明日）のものは scheduledStartDateTime を見てis-todayを付与
  if (currentDateStr === scheduledStartDateStr) {
    content.state += 'is-today';
    content.timeText = `本日${formatDateTime(content.scheduledStartDateTime, 'HH:mm')}～`;

    // ただし、配信中のものについては liveStartDateTime となるためキーが異なることに注意
    if (
      currentDateTime >= liveStartDate &&
      (currentDateTime <= liveEndDate || content.liveEndDateTime === '')
    ) {
      content.state += ' is-liveNow';
      content.timeText += `<span class="label">LIVE
            <span class="liveAnime" aria-hidden="true">
              <span class="wave">）</span>
              <span class="wave">）</span>
              <span class="wave">）</span>
            </span>
          </span>`;
    } else if (currentDateTime >= liveEndDate) {
      content.state = '';
      content.timeText = `${formatDateTime(content.scheduledStartDateTime, 'M/D HH:mm')}～`;
    }
  } else if (
    new Date(currentDateStr).getTime() + 24 * 60 * 60 * 1000 ===
    new Date(scheduledStartDateStr).getTime()
  ) {
    // 明日の配信である場合
    content.state = 'is-tomorrow';
    content.timeText = `${formatDateTime(content.scheduledStartDateTime, 'MM/DD(W)HH:mm')}～`;
  }

  return content;
}

/**
 * 優先度準にソート
 * @param {Object[]} oneDayData - 1日単位のスケジュール
 * @returns {Array[]}
 */
async function groupingByPriority(oneDayData) {
  // [ [優先度1], [優先度2], [優先度3],　[優先度4]　] に分ける
  const priority1 = await oneDayData.filter(data => data.priorityGroup === 1);
  const priority2 = await oneDayData.filter(data => data.priorityGroup === 2);
  const priority3 = await oneDayData.filter(data => data.priorityGroup === 3);
  const priority4 = await oneDayData.filter(data => data.priorityGroup === 4);
  // 時間帯にソート
  sortDataByDateTime(priority1, 'scheduledStartDateTime', false);
  sortDataByDateTime(priority2, 'scheduledStartDateTime', false);
  sortDataByDateTime(priority3, 'scheduledStartDateTime', false);
  sortDataByDateTime(priority4, 'scheduledStartDateTime', false);

  const sortedByPriority = [
    priority1, priority2, priority3, sortedByJyoCodeForSameDateTime(priority4)
  ];
  return sortedByPriority;
}

/**
 * 優先度４は同時刻のコンテンツからさらに場コードでソート
 * @param {Object[]} jyoData - 24場のデータ
 * @returns {Object[]}
 */
function sortedByJyoCodeForSameDateTime(jyoData) {
  const sortData = jyoData.sort((a, b) => {
    const dateA = new Date(a.scheduledStartDateTime)
    const dateB = new Date(b.scheduledStartDateTime)

    if (dateA - dateB !== 0) {
      return dateA - dateB
    } else {
      return a.jyoCode - b.jyoCode
    }
  });

  return sortData
}

/**
 * 動的な時間表示を更新
 * @param {Object[]} allSchedule - 結合されたデータ
 * @returns {Object[]} 選択されたデータ
 */
async function updateDynamicTimeDisplay(allSchedule) {
  const [today, tomorrow] = Object.keys(allSchedule);
  let [todayData, tomorrowData] = [allSchedule[today], allSchedule[tomorrow]];

  // 2次元配列を1次元配列に変換
  todayData = await groupingByPriority(todayData);
  tomorrowData = await groupingByPriority(tomorrowData);
  const flattenTodayData = todayData.flat(2)
  const flattenTomorrowData = tomorrowData.flat(2)
  const concatAllData = [...flattenTodayData, ...flattenTomorrowData];

  // 配信済の要素は取り除く
  const serverDateTime = await getServerDateTime();
  const currentTime = new Date(serverDateTime.replace(/-/g, '/'));

  const parseDateFromString = dateString => {
    const parts = dateString.match(/(\d+)/g);
    return new Date(parts[0], parts[1] - 1, parts[2], parts[3] || 0, parts[4] || 0, parts[5] || 0);
  };

  const displayData = concatAllData.filter(data => {
    // 値が入っていて且つ現在時刻より値が大きかった場合
    if (
      !(
        data.liveEndDateTime !== '' &&
        currentTime >= parseDateFromString(data.liveEndDateTime)
      )
    ) {
      return data
    }
  });
  // 先頭から4つまでのデータを取得
  return displayData.slice(0, 4);
}

/**
 * 配信スケジュールを初期化
 * @async
 */
async function initTimeSchedules() {
  const timeScheduleGroup = document.querySelector('.js-liveSchedulesGroup');
  const dates = await getWeekDatesFormatted(0, 2);
  const allSchedule = await fetchAllData(dates, '/assets/data/ytTimeSchedule/');
  const dataSortedByPriority =  await updateDynamicTimeDisplay(allSchedule);

  if (dataSortedByPriority.length === 0 || dataSortedByPriority[0].noContent) {
    timeScheduleGroup.innerHTML =
      '<li class="noDataBlk"><div class="noDataBlk__inner"><p class="noDataBlk__txt">配信までお待ちください</p></div></li>';
  } else {
    const populateHTML = await createTimeSchedulesHTML(dataSortedByPriority);
    timeScheduleGroup.innerHTML = populateHTML;
  }
}

/**
 * ニュース記事を初期化
 * @async
 */
async function initNewsArticles() {
  const newsArticles = document.querySelector('.js-newsArticleGroup');
  const dates = await getWeekDatesFormatted(0, 1, true);
  const allArticles = await fetchAllData(dates, '/assets/data/newsArticle/');

  dates.forEach((date, index) => {
    let articles = allArticles[date.yyyy_mm_dd];
    if (!articles) return;
    articles = sortDataByDateTime(articles, 'releaseDate', true);
    // 表示件数を6件に
    articles = articles.slice(0, 6);

    let articleHtml = articles.length ?
      createNewsArticleHTML(articles) :
      `<li class="noDataBlk">
          <div class="noDataBlk__inner">
            <p class="noDataBlk__txt noDataBlk__txt--2row">
                その日のデータはありません<br>
                サービス提供時間：8：00～21:00
            </p>
          </div>
        </li>`;

    newsArticles.innerHTML += articleHtml;
  });
}

/**
 * レースライブタブのアンカーリンクが外部からのアクセスの場合スムーススクロール無効
 * `#navRaceLive` のみ処理対象とする
 */
function checkAndScrollToHash() {
  const referrer = document.referrer;
  const hash = document.location.hash;
  const urlParams = new URLSearchParams(location.search);

  // `#navRaceLive` の場合のみ処理を実行
  if (hash === "#navRaceLive") {
    const element = document.getElementById("navRaceLive");
    if (element) {
      element.click();
      element.scrollIntoView({ behavior: referrer.startsWith(window.location.origin) ? "smooth" : "auto" });
    }
  }

  // クエリパラメータで `nav=navRaceLive` がある場合
  if (urlParams.get("nav") === "navRaceLive") {
    const element = document.getElementById("navRaceLive");
    if (element) {
      element.scrollIntoView({ behavior: "smooth" });
    }
  }
}

// spiral管理画面からトップページヘ遷移した際に検索窓にパラメータが入力されるのを削除
function searchTextClear() {
  const searchText = document.querySelector('.js-searchInput');
  if (searchText && searchText.value) {
    searchText.value = '';
  }
}

// 実行
$(async function () {
  await checkStatusDate();
  searchTextClear();
  initCarousel();
  populateMvSliderBodyWithLiveState();
  showInfoHTML();
  initTimeSchedules();
  const alwaysShowFour = true;
  const insertBySection = false;
  BoatCastNewsGroup('.js-boatcastNewsGroup', insertBySection, alwaysShowFour);
  initNewsArticles();
  movieTimeLengthDataFormatter();
  generateRankingCards();
  limitedViewCards('.js-newVideosGroup');
  displayMessageForEmptyCardGroups('更新までお待ち下さい', '.js-newVideosGroup');
  checkAndScrollToHash();
});

/**
 * レースLIVE
 * Vue.jsで対応
 */
import VenueInfoApp from "./components/VenueInfoApp.js";
const { createApp, provide, h } = Vue;

import useFetchDatetime from "./composables/fetch/useFetchDatetime.js";

const fetchToday = async () => {
    //本日日付を取得
    const { getToday } = useFetchDatetime()
    return await getToday()
}


const app = createApp({
  data() {
    return {
      today: "",
    };
  },
  async mounted() {
    // コンポーネントの初期化時にtoday値をセット
    this.today = await fetchToday();
  },
  render() {
    return h(VenueInfoApp, {
      today: this.today
    })
  },
})


app.provide("API_ENDPOINT", `${CONFIG.URL_RACE_LIVE_REPLAY}`);
app.component("venue-info-app", VenueInfoApp);
app.mount("#venueInfoApp");
