"use strict";

/* Interface translations.
 *
 * Language is chosen once per visitor: a saved choice wins, otherwise the
 * browser's own language decides (which follows the OS setting), otherwise
 * English. The switch in the header overrides it and is remembered.
 */

const I18N = {
  en: {
    _name: "English", _switch: "RU",

    login_tagline: "Beatmap recommendations built from your music taste and your skill level.",
    login_signin: "Sign in with osu!",
    err_denied: "Sign-in was cancelled.",
    err_state: "Session expired, please try again.",
    err_oauth: "Could not complete osu! sign-in. Check the app's callback URL.",
    err_generic: "Sign-in failed.",

    nav_player: "Player",
    nav_profile: "Profile",
    donate: "♥ Support",
    retake: "Retake quiz",
    retake_title: "Redo the taste questionnaire",
    retake_confirm: "Retake the taste quiz? This clears your genres and ratings.",
    logout: "Log out",

    step1: "Step 1 of 2",
    q_title: "What music do you like?",
    q_sub: "Pick at least 2 genres. We'll play you songs from them and learn your taste from your ratings.",
    continue: "Continue",
    loading_songs: "Loading songs…",

    step2: "Step 2 of 2",
    ob_text: "Listen and rate — this builds your recommendations",

    f_panel: "Filters",
    f_difficulty: "Difficulty",
    f_more: "More",
    f_auto: "Match my skill",
    f_auto_value: "auto",
    f_mods_ph: "mods (e.g. HDDT)",
    f_apply: "Apply",
    f_reset: "Reset history",
    f_reset_title: "Let previously shown maps appear again",
    f_unranked: "Include unranked maps",
    f_unranked_short: "unranked",
    f_unranked_title: "Also search graveyard and pending maps. Far more music, "
      + "but these have no leaderboard and their quality varies.",

    tier_easy: "Easy",
    tier_normal: "Normal",
    tier_hard: "Hard",
    tier_insane: "Insane",
    tier_expert: "Expert",
    tier_expertplus: "Expert+",

    status_ranked: "Ranked",
    status_approved: "Approved",
    status_qualified: "Qualified",
    status_loved: "Loved",
    status_pending: "Pending",
    status_wip: "WIP",
    status_graveyard: "Graveyard",

    btn_like: "More like this (Right arrow)",
    btn_dislike: "Not for me (Left arrow)",
    btn_play: "Play / pause (Space)",
    autoplay: "Autoplay previews",
    mapped_by: "mapped by",
    open_osu: "Open in osu! →",
    hint: "You must rate to continue — <b>←</b> dislike · <b>space</b> play/pause · <b>→</b> like",
    loading: "Building your recommendations…",
    empty_onboarding: "Couldn't load songs for those genres. Try picking different ones.",
    empty_feed: "No fresh maps match these filters. Widen the difficulty range or reset history.",
    load_error: "Something went wrong loading recommendations.",

    your_taste: "Your taste",
    taste_none: "Not enough ratings yet",
    tile_rated: "tracks rated",
    tile_liked: "liked",
    tile_rate: "like rate",
    tile_bpm: "typical BPM",
    c_genre: "Genre affinity",
    c_genre_note: "How many maps of each genre you kept versus skipped.",
    legend_liked: "Liked",
    legend_disliked: "Disliked",
    c_diff: "Difficulty you enjoy",
    c_diff_note: "Liked maps, grouped into half-star steps.",
    c_mappers: "Favourite mappers",
    c_mappers_note: "Mappers whose maps you liked most often.",
    c_recent: "Recently liked",
    profile_empty: "Rate a few more tracks in the player and your taste profile will appear here.",
    no_genre_data: "No genre data yet.",
    nothing_yet: "Nothing here yet.",
    recent_empty: "Your next likes will show up here.",
    unit_liked_maps: "liked maps",
    unit_liked: "liked",
    unit_disliked: "disliked",

    tempo_chill: "Chill", tempo_mid: "Mid-tempo", tempo_fast: "Fast", tempo_breakneck: "Breakneck",
    // {tempo} {genre} maps around {stars}★ at {bpm} BPM
    taste_line: "{tempo} {genre} around {stars}★ at {bpm} BPM",
    taste_maps: "maps",
    verdict_easier: "easier than your top plays",
    verdict_harder: "harder than your top plays",
    verdict_onpar: "right at your top-play level",
    taste_sub: "Top plays ~{skill}★ · you pick ~{taste}★ — {verdict}.",

    genres: {
      3: "Anime", 10: "Electronic", 2: "Video Game", 4: "Rock", 5: "Pop",
      11: "Metal", 9: "Hip Hop", 12: "Classical", 14: "Jazz", 13: "Folk",
      7: "Novelty", 6: "Other",
    },
  },

  ru: {
    _name: "Русский", _switch: "EN",

    login_tagline: "Подбор карт по твоему музыкальному вкусу и уровню игры.",
    login_signin: "Войти через osu!",
    err_denied: "Вход отменён.",
    err_state: "Сессия истекла, попробуй ещё раз.",
    err_oauth: "Не удалось завершить вход через osu!. Проверь callback-адрес приложения.",
    err_generic: "Не удалось войти.",

    nav_player: "Плеер",
    nav_profile: "Профиль",
    donate: "♥ Поддержать",
    retake: "Пройти заново",
    retake_title: "Пройти опрос о вкусах заново",
    retake_confirm: "Пройти опрос заново? Жанры и оценки будут удалены.",
    logout: "Выйти",

    step1: "Шаг 1 из 2",
    q_title: "Какую музыку ты любишь?",
    q_sub: "Выбери минимум 2 жанра. Мы включим песни из них и поймём твой вкус по оценкам.",
    continue: "Продолжить",
    loading_songs: "Загружаем песни…",

    step2: "Шаг 2 из 2",
    ob_text: "Слушай и оценивай — на этом строятся рекомендации",

    f_panel: "Фильтры",
    f_difficulty: "Сложность",
    f_more: "Ещё",
    f_auto: "По моему уровню",
    f_auto_value: "авто",
    f_mods_ph: "моды (напр. HDDT)",
    f_apply: "Применить",
    f_reset: "Сбросить историю",
    f_reset_title: "Разрешить снова показывать виденные карты",
    f_unranked: "Искать неранкед-карты",
    f_unranked_short: "неранкед",
    f_unranked_title: "Добавить в поиск graveyard и pending. Музыки намного больше, "
      + "но у таких карт нет таблицы рекордов, а качество разное.",

    tier_easy: "Easy",
    tier_normal: "Normal",
    tier_hard: "Hard",
    tier_insane: "Insane",
    tier_expert: "Expert",
    tier_expertplus: "Expert+",

    status_ranked: "Ранкед",
    status_approved: "Одобрена",
    status_qualified: "Квалифай",
    status_loved: "Loved",
    status_pending: "На рассмотрении",
    status_wip: "В работе",
    status_graveyard: "Graveyard",

    btn_like: "Нравится (стрелка вправо)",
    btn_dislike: "Не моё (стрелка влево)",
    btn_play: "Играть / пауза (пробел)",
    autoplay: "Автовоспроизведение",
    mapped_by: "маппер",
    open_osu: "Открыть в osu! →",
    hint: "Нужно оценить, чтобы продолжить — <b>←</b> не нравится · <b>пробел</b> пауза · <b>→</b> нравится",
    loading: "Подбираем рекомендации…",
    empty_onboarding: "Не удалось загрузить песни этих жанров. Попробуй выбрать другие.",
    empty_feed: "Нет новых карт под эти фильтры. Расширь диапазон сложности или сбрось историю.",
    load_error: "Не удалось загрузить рекомендации.",

    your_taste: "Твой вкус",
    taste_none: "Пока мало оценок",
    tile_rated: "оценено треков",
    tile_liked: "понравилось",
    tile_rate: "доля лайков",
    tile_bpm: "типичный BPM",
    c_genre: "Жанровые предпочтения",
    c_genre_note: "Сколько карт каждого жанра ты оставил, а сколько пропустил.",
    legend_liked: "Нравится",
    legend_disliked: "Не нравится",
    c_diff: "Любимая сложность",
    c_diff_note: "Понравившиеся карты по шагам в ползвезды.",
    c_mappers: "Любимые мапперы",
    c_mappers_note: "Чьи карты нравятся тебе чаще всего.",
    c_recent: "Недавно понравилось",
    profile_empty: "Оцени ещё несколько треков в плеере — и здесь появится профиль вкуса.",
    no_genre_data: "Пока нет данных по жанрам.",
    nothing_yet: "Пока пусто.",
    recent_empty: "Здесь появится то, что тебе понравится.",
    unit_liked_maps: "понравилось",
    unit_liked: "нравится",
    unit_disliked: "не нравится",

    tempo_chill: "Спокойные", tempo_mid: "Умеренные", tempo_fast: "Быстрые", tempo_breakneck: "Очень быстрые",
    taste_line: "{tempo} {genre} около {stars}★ на {bpm} BPM",
    taste_maps: "карты",
    verdict_easier: "легче твоих топ-скоров",
    verdict_harder: "сложнее твоих топ-скоров",
    verdict_onpar: "вровень с твоими топ-скорами",
    taste_sub: "Топ-скоры ~{skill}★ · выбираешь ~{taste}★ — {verdict}.",

    genres: {
      3: "Аниме", 10: "Электроника", 2: "Из игр", 4: "Рок", 5: "Поп",
      11: "Метал", 9: "Хип-хоп", 12: "Классика", 14: "Джаз", 13: "Фолк",
      7: "Новинки", 6: "Другое",
    },
  },
};

const LANG_KEY = "spotiosu.lang";
let LANG = "en";

/** Saved choice > browser/OS language > English. */
function detectLang() {
  let saved = null;
  try { saved = localStorage.getItem(LANG_KEY); } catch (_) {}
  if (saved && I18N[saved]) return saved;
  const nav = (navigator.languages && navigator.languages[0]) || navigator.language || "en";
  return nav.toLowerCase().startsWith("ru") ? "ru" : "en";
}

/** Translate a key, filling {placeholders} from `vars`. */
function t(key, vars) {
  let s = (I18N[LANG] && I18N[LANG][key]) ?? I18N.en[key] ?? key;
  if (vars) {
    for (const [k, v] of Object.entries(vars)) s = s.replaceAll(`{${k}}`, v);
  }
  return s;
}

function genreLabel(id, fallback) {
  return (I18N[LANG].genres && I18N[LANG].genres[id]) || fallback || "";
}

/** Re-render every translatable node in the document. */
function applyLang(lang) {
  LANG = I18N[lang] ? lang : "en";
  try { localStorage.setItem(LANG_KEY, LANG); } catch (_) {}
  document.documentElement.lang = LANG;

  document.querySelectorAll("[data-i18n]").forEach((el) => {
    el.textContent = t(el.dataset.i18n);
  });
  // Only for strings that legitimately contain markup (the keyboard hint).
  document.querySelectorAll("[data-i18n-html]").forEach((el) => {
    el.innerHTML = t(el.dataset.i18nHtml);
  });
  document.querySelectorAll("[data-i18n-title]").forEach((el) => {
    el.title = t(el.dataset.i18nTitle);
  });
  document.querySelectorAll("[data-i18n-ph]").forEach((el) => {
    el.placeholder = t(el.dataset.i18nPh);
  });
  document.querySelectorAll(".lang-btn").forEach((el) => {
    el.textContent = I18N[LANG]._switch;
    el.title = I18N[LANG === "en" ? "ru" : "en"]._name;
  });
}
