// ============================================
// Default server configuration
// ============================================
const DEFAULT_CONFIG = window.APP_DATA.config;
const STORAGE_KEY = "music_downloader_config";

// Track download state to warn user on page unload (reload/close)
let isDownloading = false;

function markDownloadStart() {
	isDownloading = true;
}

function markDownloadFinish() {
	isDownloading = false;
}

// Expose simple API so other scripts can mark downloads
window.OfflinerDownload = {
	start: markDownloadStart,
	finish: markDownloadFinish,
	_isDownloading: () => isDownloading,
};

// Warn the user if they try to leave while a download is in progress
window.addEventListener("beforeunload", function (e) {
	if (!isDownloading) return;
	// Most browsers ignore the returned string, but setting returnValue is required
	const confirmationMessage = "Puede perder su progreso de descarga si abandona o recarga la página.";
	e.preventDefault();
	e.returnValue = confirmationMessage;
	return confirmationMessage;
});

// ============================================
// i18n (Client-side EN/ES, no reload)
// ============================================
const LANG_STORAGE_KEY = "offliner_lang";
const DEFAULT_LANG = "en";
const translations = window.OFFLINER_TRANSLATIONS || {};

let currentLang = DEFAULT_LANG;

function normalizeLang(lang) {
	const l = String(lang || "").toLowerCase();
	if (l.startsWith("es")) return "es";
	return "en";
}

function detectBrowserLanguage() {
	return normalizeLang(navigator.language || navigator.userLanguage || DEFAULT_LANG);
}

function t(key, params = undefined) {
	const dict = translations[currentLang] || translations[DEFAULT_LANG] || {};
	const baseDict = translations[DEFAULT_LANG] || {};
	let value = dict[key] ?? baseDict[key] ?? key;
	if (params && typeof value === "string") {
		Object.entries(params).forEach(([k, v]) => {
			value = value.replaceAll(`{${k}}`, String(v));
		});
	}
	return value;
}

function applyTranslationsToDOM(root = document) {
	root.querySelectorAll("[data-i18n]").forEach((el) => {
		el.textContent = t(el.dataset.i18n);
	});
	root.querySelectorAll("[data-i18n-html]").forEach((el) => {
		el.innerHTML = t(el.dataset.i18nHtml);
	});
	root.querySelectorAll("[data-i18n-placeholder]").forEach((el) => {
		el.setAttribute("placeholder", t(el.dataset.i18nPlaceholder));
	});
	root.querySelectorAll("[data-i18n-title]").forEach((el) => {
		el.setAttribute("title", t(el.dataset.i18nTitle));
	});
}

function refreshDynamicTranslations() {
	// Update any dynamically generated controls that were rendered before switching language
	const audioText = t("common.audio");
	const videoText = t("common.video");
	// Format buttons (playlist + media preview)
	document.querySelectorAll(".format-btn-audio").forEach((btn) => {
		btn.innerHTML = `<i class="fa-solid fa-music"></i> ${audioText}`;
	});
	document.querySelectorAll(".format-btn-video").forEach((btn) => {
		btn.innerHTML = `<i class="fa-solid fa-video"></i> ${videoText}`;
	});
}

function updateLanguage(lang, { persist = true } = {}) {
	currentLang = normalizeLang(lang);
	if (persist) localStorage.setItem(LANG_STORAGE_KEY, currentLang);

	document.documentElement.setAttribute("lang", currentLang);
	applyTranslationsToDOM(document);

	// Update navbar flag
	const flagEl = document.getElementById("currentLangFlag");
	if (flagEl) {
		const src = currentLang === "es" ? '/static/img/flags/es.svg' : '/static/img/flags/us.svg';
		const alt = currentLang === "es" ? 'ES' : 'EN';
		flagEl.innerHTML = `<img src="${src}" alt="${alt}" class="lang-flag" />`;
	}

	// Update navbar language name (visible on larger screens)
	const langNameEl = document.getElementById("currentLangName");
	if (langNameEl) {
		langNameEl.textContent = currentLang === "es" ? t("lang.spanish") : t("lang.english");
	}

	// Highlight selected language in dropdown
	document.querySelectorAll("[data-lang]").forEach((btn) => {
		btn.classList.toggle("active", btn.dataset.lang === currentLang);
	});

	// Re-render dynamic UI text that is created in JS
	if (typeof updateDownloadButton === "function") updateDownloadButton();
	if (currentMediaInfo) {
		const controlsContainer = document.getElementById("mediaPreviewControls");
		if (controlsContainer && mediaPreviewFormat) {
			controlsContainer.innerHTML = createFormatControls("media-preview", mediaPreviewFormat);
		}
	}
	refreshDynamicTranslations();
}

function initLanguage() {
	const stored = localStorage.getItem(LANG_STORAGE_KEY);
	const initial = stored ? normalizeLang(stored) : detectBrowserLanguage();
	updateLanguage(initial, { persist: !!stored });

	// Navbar language selector
	document.querySelectorAll("[data-lang]").forEach((btn) => {
		btn.addEventListener("click", () => updateLanguage(btn.dataset.lang));
	});
}

// ============================================
// Funciones de localStorage para configuración
// ============================================
function getConfig() {
	try {
		const stored = localStorage.getItem(STORAGE_KEY);
		if (stored) {
			const parsed = JSON.parse(stored);
			// Merge with defaults to ensure all keys exist
			return { ...DEFAULT_CONFIG, ...parsed };
		}
	} catch (e) {
		console.error("Error reading config:", e);
	}
	return { ...DEFAULT_CONFIG };
}

function saveConfig(config) {
	try {
		localStorage.setItem(STORAGE_KEY, JSON.stringify(config));
		return true;
	} catch (e) {
		console.error("Error saving config:", e);
		return false;
	}
}

function getCurrentFormConfig() {
	const config = {};

	// Radio buttons
	document.querySelectorAll('#configForm input[type="radio"]:checked').forEach((radio) => {
		config[radio.name] = radio.value;
	});

	// Checkboxes (except SponsorBlock categories)
	document.querySelectorAll('#configForm input[type="checkbox"]:not(.sponsorblock-category)').forEach((checkbox) => {
		config[checkbox.name] = checkbox.checked;
	});

	// SponsorBlock categories
	const categories = [];
	document.querySelectorAll(".sponsorblock-category:checked").forEach((checkbox) => {
		categories.push(checkbox.value);
	});
	config["SponsorBlock_categories"] = categories;

	// Auto-detect source from the current URL
	const inputURL = document.getElementById("inputURL")?.value?.trim() || "";
	const source = detectSource(inputURL);
	config["Fuente_descarga"] = "YouTube";

	// Cookies content (Netscape format) - save the textarea content
	const cookiesEl = document.getElementById("cookiesContent");
	config["cookies_content"] = cookiesEl ? cookiesEl.value : "";

	// Add legacy keys expected elsewhere in the code (Spanish/old names)
	// Formats
	if (config.audio_format) config.Formato_audio = config.audio_format;
	if (config.video_format) config.Formato_video = config.video_format;

	// Calidad (audio/video quality)
	if (config.audio_video_quality) config.Calidad_audio_video = config.audio_video_quality;

	// Download type -> Descargar_audio/Descargar_video and Tipo_descarga
	if (config.download_type) {
		config.Tipo_descarga = config.download_type;
		config.Descargar_audio = config.download_type === "audio";
		config.Descargar_video = config.download_type === "video";
	} else {
		// sensible defaults
		config.Tipo_descarga = config.Tipo_descarga || "audio";
		config.Descargar_audio = !!config.Descargar_audio;
		config.Descargar_video = !!config.Descargar_video;
	}

	// Checkboxes legacy
	if (typeof config.scrape_metadata !== "undefined") config.Scrappear_metadata = config.scrape_metadata;
	if (typeof config.prefer_youtube_music !== "undefined") config.Preferir_YouTube_Music = config.prefer_youtube_music;
	if (typeof config.sponsorblock_enabled !== "undefined") config.SponsorBlock_enabled = config.sponsorblock_enabled;

	// SponsorBlock categories: keep both keys
	if (config.SponsorBlock_categories && Array.isArray(config.SponsorBlock_categories)) {
		config.sponsorblock_categories = config.SponsorBlock_categories;
	} else if (config.sponsorblock_categories && Array.isArray(config.sponsorblock_categories)) {
		config.SponsorBlock_categories = config.sponsorblock_categories;
	}

	// Cookies legacy
	config.cookies = config.cookies_content || config.cookies || "";

	return config;
}

function applyConfigToForm(config) {
	// Normalize incoming config keys to the DOM names we use.
	function normalizeLoadedConfig(cfg) {
		const out = {};
		// Radios: audio/video quality, audio format, video format
		out.audio_video_quality =
			cfg.audio_video_quality ?? cfg.Calidad_audio_video ?? cfg.CalidadAudioVideo ?? cfg.Calidad_audio_video ?? cfg.audio_video_quality ?? undefined;
		out.audio_format = cfg.audio_format ?? cfg.Formato_audio ?? cfg.FormatoAudio ?? undefined;
		out.video_format = cfg.video_format ?? cfg.Formato_video ?? cfg.FormatoVideo ?? undefined;

		// Download type (radio)
		out.download_type = cfg.download_type ?? cfg.Tipo_descarga ?? cfg.Tipo_descarga_val ?? undefined;

		// Checkboxes
		out.scrape_metadata = cfg.scrape_metadata ?? cfg.Scrappear_metadata ?? cfg.scrape_metadata ?? undefined;
		out.prefer_youtube_music = cfg.prefer_youtube_music ?? cfg.Preferir_YouTube_Music ?? cfg.prefer_youtube_music ?? undefined;
		out.sponsorblock_enabled = cfg.sponsorblock_enabled ?? cfg.SponsorBlock_enabled ?? cfg.sponsorblock_enabled ?? undefined;

		// SponsorBlock categories (array)
		out.sponsorblock_categories =
			cfg.sponsorblock_categories ??
			cfg.SponsorBlock_categories ??
			cfg.SponsorBlock_categories ??
			cfg.SponsorBlock_categories ??
			cfg.sponsorblock_categories ??
			[];

		// Cookies
		out.cookies_content = cfg.cookies_content ?? cfg.cookies_content ?? cfg.cookies ?? "";

		// Ensure sensible defaults when missing
		if (!out.audio_video_quality) out.audio_video_quality = "avg";
		if (!out.audio_format) out.audio_format = "mp3";
		if (!out.video_format) out.video_format = "mp4";
		if (!out.download_type) out.download_type = "audio";
		if (typeof out.scrape_metadata === "undefined") out.scrape_metadata = true;
		if (typeof out.prefer_youtube_music === "undefined") out.prefer_youtube_music = false;
		if (typeof out.sponsorblock_enabled === "undefined") out.sponsorblock_enabled = false;
		if (!Array.isArray(out.sponsorblock_categories)) out.sponsorblock_categories = [];

		return out;
	}

	const normalized = normalizeLoadedConfig(config || {});

	// Radio buttons (excluding Fuente_descarga - now auto-detected)
	["audio_video_quality", "audio_format", "video_format"].forEach((name) => {
		// First remove 'selected' class from all cards in the group
		document.querySelectorAll(`input[name="${name}"]`).forEach((r) => {
			r.closest(".option-card")?.classList.remove("selected");
		});

		const value = normalized[name];
		const radio = document.querySelector(`input[name="${name}"][value="${value}"]`);
		if (radio) {
			radio.checked = true;
			radio.closest(".option-card")?.classList.add("selected");
		}
	});

	// Checkboxes and download type radio
	["download_type", "scrape_metadata", "prefer_youtube_music", "sponsorblock_enabled"].forEach((name) => {
		if (name === "download_type") {
			// radio group
			const value = normalized.download_type;
			document.querySelectorAll(`input[name="download_type"]`).forEach((r) => r.closest(".option-card")?.classList.remove("selected"));
			const radio = document.querySelector(`input[name="download_type"][value="${value}"]`);
			if (radio) {
				radio.checked = true;
				radio.closest(".option-card")?.classList.add("selected");
			}
			return;
		}
		const checkbox = document.querySelector(`input[name="${name}"]`);
		if (checkbox) {
			checkbox.checked = !!normalized[name];
			checkbox.closest(".option-card")?.classList.toggle("selected", checkbox.checked);
		}
	});

	// SponsorBlock categories
	if (Array.isArray(normalized.sponsorblock_categories)) {
		normalized.sponsorblock_categories.forEach((cat) => {
			const checkbox = document.querySelector(`.sponsorblock-category[value="${cat}"]`);
			if (checkbox) {
				checkbox.checked = true;
				checkbox.closest(".option-card")?.classList.add("selected");
			}
		});
	}

	// Toggle SponsorBlock categories visibility
	toggleSponsorBlockCategories();

	// Restore cookies content if provided in config
	const cookiesEl = document.getElementById("cookiesContent");
	if (cookiesEl && normalized.cookies_content) {
		cookiesEl.value = normalized.cookies_content;
	}
}

// Validate that the text is a valid Netscape cookies file (or empty)
function isValidNetscapeCookies(text) {
	if (!text) return true; // vacío permitido
	const lines = text.split(/\r?\n/);
	for (let raw of lines) {
		const line = raw.trim();
		if (!line) continue; // saltar líneas vacías
		if (line.startsWith("#")) continue; // comentarios permitidos

		// Preferir tab-separated fields, pero aceptar whitespace
		let parts = line.split("\t");
		if (parts.length !== 7) parts = line.split(/\s+/);
		// Netscape spec is 7 fields, but some exports omit the final value column.
		if (parts.length === 6) parts.push("");
		if (parts.length !== 7) return false;

		const [domain, flag, path, secure, expiration, name, value] = parts.map((p) => p.trim());

		if (!name) return false;

		if (!/^[0-9]+$/.test(expiration)) return false;

		const boolPattern = /^(true|false|TRUE|FALSE|0|1)$/;
		if (!boolPattern.test(secure)) return false;
		if (!boolPattern.test(flag)) return false;
	}
	return true;
}

// ============================================
// Auto-detect source from URL
// ============================================
function detectSource(url) {
	if (!url) return null;
	const urlLower = url.toLowerCase();

	if (urlLower.includes("spotify.com")) {
		return "spotify";
	} else if (urlLower.includes("music.youtube.com")) {
		return "youtube_music";
	} else if (urlLower.includes("youtube.com") || urlLower.includes("youtu.be")) {
		return "youtube";
	}
	return null;
}

function isValidURL(url) {
	return detectSource(url) !== null;
}

// ============================================
// Media Preview System
// ============================================
let mediaPreviewTimeout = null;
let currentMediaInfo = null;
let isProcessingInput = false;

// ============================================
// Unified Format Controls Generation
// ============================================

function createFormatControls(url, currentFormat) {
	const currentConfig = getCurrentFormConfig();
	const audioFormats = ["mp3", "m4a", "flac", "wav"];
	const videoFormats = ["mp4", "mkv", "webm", "mov"];
	const formats = currentFormat === "audio" ? audioFormats : videoFormats;
	const globalFormat = currentFormat === "audio" ? currentConfig.Formato_audio || "mp3" : currentConfig.Formato_video || "mp4";
	const audioLabel = t("common.audio");
	const videoLabel = t("common.video");

	return `
		<div class="unified-format-controls">
			<div class="btn-group btn-group-sm" role="group">
				<button type="button" class="btn btn-outline-secondary format-btn-audio ${currentFormat === "audio" ? "active" : ""}" 
						data-url="${url}" onclick="setItemFormatType(event, '${url}', 'audio')">
					<i class="fa-solid fa-music"></i> ${audioLabel}
				</button>
				<button type="button" class="btn btn-outline-secondary format-btn-video ${currentFormat === "video" ? "active" : ""}" 
						data-url="${url}" onclick="setItemFormatType(event, '${url}', 'video')">
					<i class="fa-solid fa-video"></i> ${videoLabel}
				</button>
			</div>
			<div class="custom-dropdown ms-2" data-url="${url}">
				<button type="button" class="custom-dropdown-toggle" onclick="toggleCustomDropdown(event)">
					<span class="dropdown-value">${globalFormat.toUpperCase()}</span>
					<i class="fa-solid fa-chevron-down"></i>
				</button>
				<div class="custom-dropdown-menu">
					${formats.map((f) => `<div class="custom-dropdown-item${f === globalFormat ? " selected" : ""}" data-value="${f}" onclick="selectDropdownItem(event, '${url}')">${f.toUpperCase()}</div>`).join("")}
				</div>
			</div>
		</div>
	`;
}

function setItemFormatType(event, url, format) {
	event.preventDefault();
	event.stopPropagation();

	// Update the format
	if (url === "media-preview") {
		mediaPreviewFormat = format;
	} else {
		itemCustomFormats.set(url, format);
	}

	// Update button states
	const container = event.target.closest(".unified-format-controls");
	if (container) {
		const audioBtn = container.querySelector(".format-btn-audio");
		const videoBtn = container.querySelector(".format-btn-video");

		if (format === "audio") {
			audioBtn?.classList.add("active");
			videoBtn?.classList.remove("active");
		} else {
			videoBtn?.classList.add("active");
			audioBtn?.classList.remove("active");
		}
	}

	// Update file format dropdown
	updateFileFormatDropdown(url, format);

	const formatText = format.toUpperCase();
	showToast({ key: "toast.formatSet", params: { format: formatText } }, "success");
	return true;
}

function updateFileFormatDropdown(url, mediaType) {
	const dropdown = document.querySelector(`.custom-dropdown[data-url="${url}"]`);
	if (!dropdown) return;

	const currentConfig = getCurrentFormConfig();
	const audioFormats = ["mp3", "m4a", "flac", "wav"];
	const videoFormats = ["mp4", "mkv", "webm", "mov"];

	const currentValue = url === "media-preview" ? mediaPreviewFileFormat : itemFileFormats.get(url);
	const menu = dropdown.querySelector(".custom-dropdown-menu");
	const valueSpan = dropdown.querySelector(".dropdown-value");

	const formats = mediaType === "audio" ? audioFormats : videoFormats;
	const globalFormat = mediaType === "audio" ? currentConfig.Formato_audio || "mp3" : currentConfig.Formato_video || "mp4";

	// Clear and rebuild menu
	menu.innerHTML = "";
	formats.forEach((format) => {
		const item = document.createElement("div");
		item.className = "custom-dropdown-item" + (format === globalFormat && !currentValue ? " selected" : "");
		item.dataset.value = format;
		item.textContent = format.toUpperCase();
		item.setAttribute("onclick", `selectDropdownItem(event, '${url}')`);
		menu.appendChild(item);
	});

	// Restore previous value if still valid, otherwise use global config format
	if (currentValue && formats.includes(currentValue)) {
		valueSpan.textContent = currentValue.toUpperCase();
	} else {
		valueSpan.textContent = globalFormat.toUpperCase();
		if (url === "media-preview") {
			mediaPreviewFileFormat = null;
		} else {
			itemFileFormats.delete(url);
		}
	}
}

function showMediaPreview(info) {
	const preview = document.getElementById("mediaPreview");
	const content = document.getElementById("mediaPreviewContent");
	const loading = document.getElementById("mediaPreviewLoading");

	// Hide loading and show content
	loading.style.display = "none";
	content.style.display = "flex";

	// Update information
	document.getElementById("mediaThumbnail").src = info.thumbnail || "https://via.placeholder.com/120x90?text=No+Image";
	document.getElementById("mediaTitle").textContent = info.titulo || t("media.untitled");
	document.getElementById("mediaAuthor").textContent = info.autor || t("media.unknown");
	document.getElementById("mediaDuration").textContent = info.duracion || "0:00";

	// Update source badge
	const currentConfig = getCurrentFormConfig();
	const sourceBadge = document.getElementById("mediaSource");

	// Determine display text and icon based on config and source
	let sourceText, sourceIcon, sourceClass;

	if (info.fuente === "spotify") {
		sourceText = "Spotify";
		sourceIcon = "fa-brands fa-spotify";
		sourceClass = "spotify";
	} else if (currentConfig.Preferir_YouTube_Music && info.fuente === "youtube_music") {
		sourceText = "YT Music";
		sourceIcon = "fa-brands fa-youtube";
		sourceClass = "youtube_music";
	} else if (currentConfig.Preferir_YouTube_Music && info.fuente === "youtube") {
		sourceText = "YT Music";
		sourceIcon = "fa-brands fa-youtube";
		sourceClass = "youtube_music";
	} else {
		sourceText = "YouTube";
		sourceIcon = "fa-brands fa-youtube";
		sourceClass = "youtube";
	}

	sourceBadge.className = "source-badge-inline " + sourceClass;
	sourceBadge.innerHTML = `<i class="${sourceIcon}"></i> ${sourceText}`;

	// Initialize format controls
	const defaultFormat = currentConfig.Descargar_video ? "video" : "audio";
	mediaPreviewFormat = defaultFormat;
	mediaPreviewFileFormat = null;

	// Generate unified controls using the same function as playlist items
	const controlsContainer = document.getElementById("mediaPreviewControls");
	if (controlsContainer) {
		controlsContainer.innerHTML = createFormatControls("media-preview", defaultFormat);
	}

	// Show preview with animation
	preview.classList.add("visible");
	currentMediaInfo = info;

	// Ensure download button state reflects the media preview
	if (typeof updateDownloadButton === "function") updateDownloadButton();

	// Fetch SponsorBlock info if applicable
	if (info.video_id) {
		fetchSponsorBlockForMediaPreview(info);
	}
}

async function fetchSponsorBlockForMediaPreview(info) {
	const currentConfig = getCurrentFormConfig();

	// Only fetch if SponsorBlock is enabled
	if (!currentConfig.SponsorBlock_enabled) {
		return;
	}

	const categories = currentConfig.SponsorBlock_categories || [];
	if (categories.length === 0) {
		return;
	}

	try {
		const formData = new FormData();
		formData.append("video_id", info.video_id);
		formData.append("categories", JSON.stringify(categories));
		formData.append("duration", info.duracion_segundos || 0);
		formData.append("csrf_token", window.APP_DATA.csrfToken);

		const response = await fetch("/sponsorblock_info", {
			method: "POST",
			body: formData,
		});

		const data = await response.json();

		if (data.success && data.has_segments) {
			// Update duration display
			const durationEl = document.getElementById("mediaDuration");
			if (durationEl) {
				const original = info.duracion;
				durationEl.innerHTML = `<span class="text-decoration-line-through text-muted">${original}</span> → ${data.adjusted_duration_str}`;
			}

			// Show SponsorBlock badge
			const sbBadge = document.getElementById("mediaSBBadge");
			const sbDuration = document.getElementById("mediaSBDuration");
			if (sbBadge && sbDuration) {
				sbDuration.textContent = data.adjusted_duration_str || sbDuration.textContent || "SB";
				sbBadge.style.display = "inline-flex";
			}
			// Show SponsorBlock indicator toast
			showToast({ key: "toast.sponsorblockRemoved", params: { count: data.segment_count } }, "info");
		}
	} catch (error) {
		console.error("Error fetching SponsorBlock for media preview:", error);
	}
}

function hideMediaPreview() {
	const preview = document.getElementById("mediaPreview");
	preview.classList.remove("visible");
	currentMediaInfo = null;

	// Update download button state
	if (typeof updateDownloadButton === "function") updateDownloadButton();
}

function showMediaPreviewLoading() {
	const preview = document.getElementById("mediaPreview");
	const content = document.getElementById("mediaPreviewContent");
	const loading = document.getElementById("mediaPreviewLoading");

	content.style.display = "none";
	loading.style.display = "flex";
	preview.classList.add("visible");
}

async function fetchMediaInfo(url) {
	try {
		showMediaPreviewLoading();

		const formData = new FormData();
		formData.append("url", url);
		formData.append("user_config", JSON.stringify(getCurrentFormConfig()));
		formData.append("csrf_token", window.APP_DATA.csrfToken);

		const response = await fetch("/media_info", {
			method: "POST",
			body: formData,
		});

		const data = await response.json();

		if (data.es_playlist) {
			// Es playlist, ocultar preview y dejar que el sistema de playlist lo maneje
			hideMediaPreview();
			return { es_playlist: true, fuente: data.fuente };
		}

		if (data.success) {
			showMediaPreview(data);
			return { es_playlist: false, info: data };
		} else {
			hideMediaPreview();
			return null;
		}
	} catch (error) {
		console.error("Error fetching media info:", error);
		hideMediaPreview();
		return null;
	}
}

// Close media preview
document.getElementById("closeMediaPreview")?.addEventListener("click", function () {
	hideMediaPreview();
});

// ============================================
// Custom Dropdown Functionality
// ============================================

// Close dropdowns when clicking outside
document.addEventListener("click", function (e) {
	if (!e.target.closest(".custom-dropdown")) {
		document.querySelectorAll(".custom-dropdown").forEach((dropdown) => {
			dropdown.classList.remove("active");
		});
	}
});

function toggleCustomDropdown(event) {
	event.preventDefault();
	event.stopPropagation();

	const dropdown = event.target.closest(".custom-dropdown");
	const wasActive = dropdown.classList.contains("active");

	// Close all dropdowns
	document.querySelectorAll(".custom-dropdown").forEach((dd) => {
		dd.classList.remove("active");
	});

	// Toggle current dropdown
	if (!wasActive) {
		dropdown.classList.add("active");
	}
}

function selectDropdownItem(event, url) {
	event.preventDefault();
	event.stopPropagation();

	const item = event.target;
	const value = item.dataset.value;
	const dropdown = item.closest(".custom-dropdown");
	const valueSpan = dropdown.querySelector(".dropdown-value");

	// Update display
	valueSpan.textContent = item.textContent;

	// Update selected state
	dropdown.querySelectorAll(".custom-dropdown-item").forEach((i) => i.classList.remove("selected"));
	item.classList.add("selected");

	// Close dropdown
	dropdown.classList.remove("active");

	// Set the file format
	if (url === "media-preview") {
		mediaPreviewFileFormat = value;
	} else {
		setItemFileFormat(url, value);
	}
}

// Media preview format controls
let mediaPreviewFormat = null;
let mediaPreviewFileFormat = null;

// ============================================
// Toast System
// ============================================
const TOAST_DURATION = 2500; // ms — auto-dismiss delay
const TOAST_EXIT_MS = 350; // must match CSS toastSlideOut duration

function resolveI18nMessage(message, params) {
	if (message && typeof message === "object" && message.key) {
		return t(message.key, message.params ?? params);
	}
	if (typeof message === "string" && message.startsWith("i18n:")) {
		return t(message.slice("i18n:".length), params);
	}
	return String(message ?? "");
}

function showToast(message, type = "primary", params = undefined) {
	const container = document.getElementById("toastContainer");
	const toastId = "toast-" + Date.now() + "-" + Math.random().toString(36).slice(2, 6);
	const resolvedMessage = resolveI18nMessage(message, params);

	// Map legacy "primary" → "info" for the CSS class, keep others as-is
	const cssType = type === "primary" ? "info" : type;

	const icons = {
		primary: "fa-circle-info",
		info: "fa-circle-info",
		success: "fa-circle-check",
		danger: "fa-circle-exclamation",
		warning: "fa-triangle-exclamation",
	};

	const toastHtml = `
		<div id="${toastId}" class="toast toast-custom toast-${cssType} toast-slide-in show" role="alert">
			<div class="d-flex align-items-center">
				<div class="toast-body">
					<i class="fa-solid ${icons[type] || icons.info}"></i>
					${resolvedMessage}
				</div>
				<button type="button" class="btn-close me-2 m-auto" onclick="closeToast('${toastId}')" aria-label="Close"></button>
			</div>
			<div class="toast-progress-track">
				<div class="toast-progress-bar" style="animation-duration: ${TOAST_DURATION}ms;"></div>
			</div>
		</div>
	`;

	container.insertAdjacentHTML("beforeend", toastHtml);

	const toastEl = document.getElementById(toastId);

	// Track elapsed time so we can pause/resume without resetting
	let remainingMs = TOAST_DURATION;
	let timerStart = Date.now();
	let autoTimer = setTimeout(() => closeToast(toastId), remainingMs);

	toastEl.addEventListener("mouseenter", () => {
		// Pause: clear JS timer, save remaining time
		clearTimeout(autoTimer);
		remainingMs -= Date.now() - timerStart;
		if (remainingMs < 0) remainingMs = 0;
		// CSS handles pausing the progress bar via animation-play-state: paused
	});

	toastEl.addEventListener("mouseleave", () => {
		if (remainingMs <= 0) {
			closeToast(toastId);
			return;
		}
		// Resume: restart JS timer with remaining time
		// CSS automatically resumes the progress bar animation via animation-play-state
		timerStart = Date.now();
		autoTimer = setTimeout(() => closeToast(toastId), remainingMs);
	});
	autoTimer = setTimeout(() => closeToast(toastId), remainingMs);
}

function closeToast(toastId) {
	const toast = document.getElementById(toastId);
	if (!toast || toast.dataset.closing) return;
	toast.dataset.closing = "true";
	toast.classList.remove("toast-slide-in");
	toast.classList.add("toast-slide-out");
	setTimeout(() => toast.remove(), TOAST_EXIT_MS);
}

// ============================================
// Inicialización al cargar la página
// ============================================
document.addEventListener("DOMContentLoaded", function () {
	// i18n: auto-detect browser language (es/* => ES, otherwise EN) and restore persisted choice
	initLanguage();

	// Cargar configuración guardada
	const savedConfig = getConfig();
	applyConfigToForm(savedConfig);

	// Actualizar el input hidden con la configuración
	document.getElementById("userConfigInput").value = JSON.stringify(savedConfig);

	// Guardar/actualizar el contenido de cookies en localStorage cuando el usuario lo edite
	const cookiesEl = document.getElementById("cookiesContent");
	if (cookiesEl) {
		cookiesEl.addEventListener("input", function () {
			const val = this.value;
			const valid = isValidNetscapeCookies(val);
			this.classList.toggle("is-invalid", !valid);
			if (!valid) {
				// Do not save invalid content
				return;
			}
			const current = getConfig();
			current.cookies_content = val;
			saveConfig(current);
			// Mantener el input hidden sincronizado
			document.getElementById("userConfigInput").value = JSON.stringify(current);
		});
	}

	// ============================================
	// Config Form - Guardar en localStorage
	// ============================================
	document.getElementById("saveConfigBtn").addEventListener("click", function (e) {
		e.preventDefault();

		const btn = this;
		const originalContent = btn.innerHTML;

		// Show loading state
		btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>' + t("btn.saving");
		btn.disabled = true;

		const config = getCurrentFormConfig();

		// Validar cookies antes de guardar
		if (!isValidNetscapeCookies(config.cookies_content)) {
			showToast({ key: "toast.invalidCookies" }, "warning");
			// Restore button
			btn.innerHTML = originalContent;
			btn.disabled = false;
			return;
		}

		setTimeout(() => {
			if (saveConfig(config)) {
				showToast({ key: "toast.settingsSaved" }, "success");
				// Actualizar el input hidden
				document.getElementById("userConfigInput").value = JSON.stringify(config);

				// Cerrar el modal automáticamente
				const modalElement = document.getElementById("configModal");
				let modalInstance = bootstrap.Modal.getInstance(modalElement);
				if (!modalInstance) {
					modalInstance = new bootstrap.Modal(modalElement);
				}
				modalInstance.hide();
			} else {
				showToast({ key: "toast.settingsSaveError" }, "danger");
			}

			// Restore button
			btn.innerHTML = originalContent;
			btn.disabled = false;
		}, 300);
	});

	// ============================================
	// Option card selection highlighting
	// ============================================
	document.querySelectorAll('.option-card input[type="radio"]').forEach((input) => {
		input.addEventListener("change", function () {
			// Remove selected from siblings
			const name = this.name;
			document.querySelectorAll(`input[name="${name}"]`).forEach((sibling) => {
				sibling.closest(".option-card").classList.remove("selected");
			});
			// Add selected to current
			this.closest(".option-card").classList.add("selected");
		});
	});

	document.querySelectorAll('.option-card input[type="checkbox"]').forEach((input) => {
		input.addEventListener("change", function () {
			this.closest(".option-card").classList.toggle("selected", this.checked);
		});
	});

	// Ensure download button reflects initial state
	if (typeof updateDownloadButton === "function") updateDownloadButton();
});

// ============================================
// Toggle functions
// ============================================
function toggleSponsorBlockCategories() {
	const checkbox = document.getElementById("sponsorblockEnabled");
	const categories = document.getElementById("sponsorblockCategories");
	const card = checkbox.closest(".option-card");

	const enabled = checkbox.checked;
	card.classList.toggle("selected", enabled);

	// Toggle disabled visual state instead of hiding
	categories.classList.toggle("sponsorblock-disabled", !enabled);

	// Disable/enable all category checkboxes
	categories.querySelectorAll(".sponsorblock-category").forEach((cb) => {
		cb.disabled = !enabled;
	});
}

// ============================================
// Playlist System
// ============================================
let playlistData = null;
let selectedItems = new Set();
let playlistCheckTimeout = null;

// Function to check if a URL is a playlist
function isPlaylistURL(url) {
	if (!url) return false;
	const urlLower = url.toLowerCase();
	return (
		urlLower.includes("list=") ||
		urlLower.includes("/playlist") ||
		(urlLower.includes("spotify.com") && (urlLower.includes("/playlist/") || urlLower.includes("/album/")))
	);
}

// Auto-detect URL while typing
document.getElementById("inputURL").addEventListener("input", function () {
	// If we're processing a paste event, ignore input
	if (isProcessingInput) return;

	const url = this.value.trim();

	// Clear previous timeouts if present
	if (playlistCheckTimeout) {
		clearTimeout(playlistCheckTimeout);
	}
	if (mediaPreviewTimeout) {
		clearTimeout(mediaPreviewTimeout);
	}

	// If the field is empty, hide everything
	if (!url) {
		hideMediaPreview();
		if (playlistData) {
			document.getElementById("playlistContainer").style.display = "none";
			playlistData = null;
			selectedItems.clear();
			updateDownloadButton();
		}
		return;
	}

	// If it's not a valid URL, hide previews
	if (!isValidURL(url)) {
		hideMediaPreview();
		if (playlistData) {
			document.getElementById("playlistContainer").style.display = "none";
			playlistData = null;
			selectedItems.clear();
			updateDownloadButton();
		}
		return;
	}

	// If it's a playlist URL
	if (isPlaylistURL(url)) {
		hideMediaPreview();
		playlistCheckTimeout = setTimeout(() => {
			checkForPlaylist();
		}, 800);
	} else {
		// Single item URL - show preview
		if (playlistData) {
			document.getElementById("playlistContainer").style.display = "none";
			playlistData = null;
			selectedItems.clear();
			updateDownloadButton();
		}
		mediaPreviewTimeout = setTimeout(async () => {
			await fetchMediaInfo(url);
		}, 800);
	}
});

// Also check when pasting a URL
document.getElementById("inputURL").addEventListener("paste", function () {
	// Mark that we're processing to avoid duplicates
	isProcessingInput = true;

	// Cancel previous timeouts
	if (playlistCheckTimeout) clearTimeout(playlistCheckTimeout);
	if (mediaPreviewTimeout) clearTimeout(mediaPreviewTimeout);

	setTimeout(async () => {
		const url = this.value.trim();
		if (!url) {
			isProcessingInput = false;
			return;
		}

		if (!isValidURL(url)) {
			hideMediaPreview();
			isProcessingInput = false;
			return;
		}

		if (isPlaylistURL(url)) {
			hideMediaPreview();
			await checkForPlaylist();
		} else {
			// Single item URL - fetch info
			await fetchMediaInfo(url);
		}

		isProcessingInput = false;
	}, 150);
});

// Search Button Logic
document.getElementById("searchBtn").addEventListener("click", performSearch);
document.getElementById("inputURL").addEventListener("keydown", function (e) {
	if (e.key === "Enter") {
		e.preventDefault();
		performSearch();
	}
});

async function performSearch() {
	const input = document.getElementById("inputURL");
	const query = input.value.trim();

	if (!query) {
		showToast({ key: "toast.enterUrlOrSearch" }, "warning");
		return;
	}

	// If it's a valid URL, use the existing flow
	if (isValidURL(query)) {
		if (isPlaylistURL(query)) {
			await checkForPlaylist();
		} else {
			await fetchMediaInfo(query);
		}
		return;
	}

	// Si es texto, buscar en YouTube
	hideMediaPreview();

	// Mostrar loading en playlist container
	const container = document.getElementById("playlistContainer");
	const loading = document.getElementById("playlistLoading");
	const itemsContainer = document.getElementById("playlistItemsContainer");

	if (container) container.style.display = "block";
	// Re-insert skeletons into the scrollable items container so they're shown every search
	if (itemsContainer) {
		itemsContainer.innerHTML = renderPlaylistSkeletons(5);
		itemsContainer.style.display = "block";
	}
	// Re-resolve loading after injecting skeletons
	const newLoading = document.getElementById("playlistLoading");
	if (newLoading) newLoading.style.display = "block";
	const titleEl = document.getElementById("playlistTitle");
	if (titleEl) titleEl.textContent = t("search.searching", { query });
	const metaEl = document.getElementById("playlistMeta");
	if (metaEl) metaEl.textContent = t("search.searchingYouTube");
	const thumbEl = document.getElementById("playlistThumbnail");
	if (thumbEl) thumbEl.style.display = "none";

	try {
		const formData = new FormData();
		formData.append("query", query);
		formData.append("csrf_token", window.APP_DATA.csrfToken);

		// Check if YTM preference is enabled
		const preferYTM =
			document.querySelector('input[name="prefer_youtube_music"]')?.checked || document.querySelector('input[name="Preferir_YouTube_Music"]')?.checked;
		formData.append("prefer_ytmusic", preferYTM ? "true" : "false");

		const response = await fetch(window.APP_DATA.urls.search_youtube, {
			method: "POST",
			body: formData,
		});

		const data = await response.json();

		if (data.success && data.playlist) {
			// Mark this playlist object as a search result so UI doesn't auto-select items
			data.playlist.is_search = true;
			showPlaylistSelector(data.playlist);
		} else {
			container.style.display = "none";
			if (data.error) {
				showToast(data.error, "warning");
			} else {
				showToast({ key: "toast.noResults" }, "warning");
			}
		}
	} catch (error) {
		console.error("Search error:", error);
		container.style.display = "none";
		showToast({ key: "toast.searchError" }, "danger");
	}
}

// Render skeleton HTML for the playlist items area
function renderPlaylistSkeletons(count = 5) {
	let html = '<div id="playlistLoading">';
	for (let i = 0; i < count; i++) {
		html += '<div class="skeleton skeleton-item"></div>';
	}
	html += "</div>";
	return html;
}

async function checkForPlaylist() {
	const url = document.getElementById("inputURL").value.trim();
	if (!url || !isPlaylistURL(url)) {
		return;
	}

	// Mostrar indicador de carga en el contenedor de playlist
	const container = document.getElementById("playlistContainer");
	const loading = document.getElementById("playlistLoading");
	const itemsContainer = document.getElementById("playlistItemsContainer");

	// Mostrar loading (skeletons are inside itemsContainer so show it)
	if (container) container.style.display = "block";
	if (itemsContainer) {
		itemsContainer.innerHTML = renderPlaylistSkeletons(5);
		itemsContainer.style.display = "block";
	}
	const newLoading2 = document.getElementById("playlistLoading");
	if (newLoading2) newLoading2.style.display = "block";
	const titleEl2 = document.getElementById("playlistTitle");
	if (titleEl2) titleEl2.textContent = t("playlist.loading");
	const metaEl2 = document.getElementById("playlistMeta");
	if (metaEl2) metaEl2.textContent = t("media.gettingInfo");
	const thumbEl2 = document.getElementById("playlistThumbnail");
	if (thumbEl2) thumbEl2.style.display = "none";

	try {
		const formData = new FormData();
		formData.append("url", url);
		formData.append("user_config", JSON.stringify(getCurrentFormConfig()));
		formData.append("csrf_token", window.APP_DATA.csrfToken);

		const response = await fetch(window.APP_DATA.urls.playlist_info, {
			method: "POST",
			body: formData,
		});

		const data = await response.json();

		if (data.success && data.playlist) {
			showPlaylistSelector(data.playlist);
		} else if (data.es_playlist === false) {
			container.style.display = "none";
			showToast({ key: "toast.notPlaylist" }, "info");
		} else {
			container.style.display = "none";
			if (data.error) {
				showToast(data.error, "danger");
			} else {
				showToast({ key: "toast.couldNotGetPlaylist" }, "danger");
			}
		}
	} catch (error) {
		console.error("Error:", error);
		container.style.display = "none";
		showToast({ key: "toast.checkPlaylistError" }, "danger");
	}
}

function showPlaylistSelector(playlist) {
	playlistData = playlist;
	// For regular playlists (actual playlist/album URLs) keep previous behavior (select all by default).
	// For search results (marked with is_search) start with nothing selected so user can choose.
	if (playlist.is_search) {
		selectedItems = new Set();
	} else {
		selectedItems = new Set(playlist.items.map((item) => item.url)); // Select all by default
	}

	const container = document.getElementById("playlistContainer");
	const loading = document.getElementById("playlistLoading");
	const itemsContainer = document.getElementById("playlistItemsContainer");

	// Update header info (defensive access)
	const titleEl = document.getElementById("playlistTitle");
	if (titleEl) titleEl.textContent = playlist.titulo;
	const metaEl = document.getElementById("playlistMeta");
	if (metaEl)
		metaEl.textContent = t("playlist.meta", {
			count: playlist.total,
			videos: t("playlist.videosLabel"),
			author: playlist.autor,
		});

	if (playlist.thumbnail) {
		const thumb = document.getElementById("playlistThumbnail");
		if (thumb) {
			thumb.src = playlist.thumbnail;
			thumb.style.display = "block";
		}
	}

	// Show container with loading (skeletons are inside the scrollable items container)
	if (container) container.style.display = "block";
	if (loading) loading.style.display = "block";
	if (itemsContainer) itemsContainer.style.display = "block";

	// Build items HTML
	let html = "";
	playlist.items.forEach((item, index) => {
		const isSelected = selectedItems.has(item.url);
		// Default format from global config
		const currentConfig = getCurrentFormConfig();
		const defaultFormat = currentConfig.Descargar_video ? "video" : "audio";
		const itemFormat = item.customFormat || defaultFormat;

		// Detect source from item URL
		let sourceText, sourceIcon, sourceClass;
		if (item.url && item.url.toLowerCase().includes("spotify.com")) {
			sourceText = "Spotify";
			sourceIcon = "fa-brands fa-spotify";
			sourceClass = "spotify";
		} else if (currentConfig.Preferir_YouTube_Music) {
			sourceText = "YT Music";
			sourceIcon = "fa-brands fa-youtube";
			sourceClass = "youtube_music";
		} else {
			sourceText = "YouTube";
			sourceIcon = "fa-brands fa-youtube";
			sourceClass = "youtube";
		}

		const badgeHtml = `<span class="source-badge-inline ${sourceClass}"><i class="${sourceIcon}"></i> ${sourceText}</span>`;

		html += `
				<div class="media-item ${isSelected ? "selected" : ""}" data-url="${item.url}" data-duration="${item.duracion_segundos}" data-index="${index}">
					<span class="badge bg-secondary media-item-number">${index + 1}</span>
					<input type="checkbox" class="form-check-input media-item-checkbox" 
						${isSelected ? "checked" : ""} data-url="${item.url}" 
						onchange="togglePlaylistItem(this)">
                <img src="${item.thumbnail || "https://via.placeholder.com/120x90?text=No+Image"}" 
                     alt="${item.titulo}" class="media-item-thumbnail"
                     onerror="this.src='https://via.placeholder.com/120x90?text=No+Image'">
                <div class="media-item-info">
                    <div class="media-item-title" title="${item.titulo}">${item.titulo}</div>
                    <div class="media-item-meta">
						${badgeHtml}
                        <span class="mx-2">•</span>
                        <i class="fa-solid fa-user me-1"></i>
						<span>${item.autor || t("media.unknown")}</span>
                        <span class="mx-2">•</span>
                        <i class="fa-solid fa-clock me-1"></i>
                        <span class="item-duration" data-original="${item.duracion}">${item.duracion}</span>
                        ${
							item.video_id
								? `<span class="item-sb-badge-inline badge bg-warning text-dark ms-2" data-url="${item.url}" data-video-id="${item.video_id}" style="display:none;">
                            <i class="fa-solid fa-scissors"></i>
                            <span class="sb-duration">...</span>
                        </span>`
								: ""
						}
                    </div>
                    <div class="media-item-controls mt-2">
                        ${createFormatControls(item.url, itemFormat)}
                    </div>
                </div>
            </div>
        `;
	});

	// Update UI
	setTimeout(() => {
		if (loading) loading.style.display = "none";
		if (itemsContainer) {
			itemsContainer.innerHTML = html;
			itemsContainer.style.display = "block";
		}
		updatePlaylistCounts();
		updateDownloadButton();

		// Fetch SponsorBlock info for items with video_id
		fetchSponsorBlockForPlaylist(playlist);
	}, 500);
}

function togglePlaylistItem(checkbox) {
	const url = checkbox.dataset.url;
	const item = checkbox.closest(".media-item");

	if (checkbox.checked) {
		selectedItems.add(url);
		item.classList.add("selected");
	} else {
		selectedItems.delete(url);
		item.classList.remove("selected");
	}

	updatePlaylistCounts();
	updateDownloadButton();
}

function updatePlaylistCounts() {
	const selectedCount = selectedItems.size;
	const totalCount = playlistData ? playlistData.total : 0;

	document.getElementById("selectedCount").textContent = selectedCount;
	document.getElementById("totalCount").textContent = totalCount;

	// Calculate total duration
	let totalSeconds = 0;
	if (playlistData) {
		playlistData.items.forEach((item) => {
			if (selectedItems.has(item.url)) {
				totalSeconds += item.duracion_segundos || 0;
			}
		});
	}

	const hours = Math.floor(totalSeconds / 3600);
	const minutes = Math.floor((totalSeconds % 3600) / 60);
	const seconds = totalSeconds % 60;

	let durationStr = "";
	if (hours > 0) {
		durationStr = `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
	} else {
		durationStr = `${minutes}:${String(seconds).padStart(2, "0")}`;
	}

	document.getElementById("totalDuration").textContent = durationStr;
}

function updateDownloadButton() {
	const btnText = document.getElementById("downloadBtnText");
	const downloadBtn = document.getElementById("downloadBtn");

	// Prioritize playlist mode: if we have playlist data, button enabled only when items selected
	if (playlistData !== null) {
		if (selectedItems.size > 0) {
			btnText.textContent = t("btn.downloadCount", { count: selectedItems.size });
			downloadBtn.disabled = false;
			downloadBtn.classList.remove("btn-secondary");
			downloadBtn.classList.add("btn-primary");
		} else {
			// Playlist with no selection -> disable
			btnText.textContent = t("btn.startDownload");
			downloadBtn.disabled = true;
			downloadBtn.classList.remove("btn-primary");
			downloadBtn.classList.add("btn-secondary");
		}
	} else if (currentMediaInfo !== null) {
		// Single media preview available -> enable
		btnText.textContent = t("btn.startDownload");
		downloadBtn.disabled = false;
		downloadBtn.classList.remove("btn-secondary");
		downloadBtn.classList.add("btn-primary");
	} else {
		// No selection and no media -> disabled
		btnText.textContent = t("btn.startDownload");
		downloadBtn.disabled = true;
		downloadBtn.classList.remove("btn-primary");
		downloadBtn.classList.add("btn-secondary");
	}

	// Hidden input expects a boolean indicating whether we're in playlist mode with selections
	const isPlaylistMode = playlistData !== null && selectedItems.size > 0;
	document.getElementById("isPlaylistModeInput").value = isPlaylistMode ? "true" : "false";
	document.getElementById("selectedUrlsInput").value = isPlaylistMode ? JSON.stringify(Array.from(selectedItems)) : "";
}

// ============================================
// Individual Item Format Toggle
// ============================================
const itemCustomFormats = new Map(); // Store custom format for each item URL
const itemFileFormats = new Map(); // Store file format (mp3, mp4, etc) for each item URL

function setItemFileFormat(url, format) {
	itemFileFormats.set(url, format);
}

function getItemCustomFormats() {
	// Return custom formats as object for submission
	const currentConfig = getCurrentFormConfig();
	const formats = {};

	// Build config for every item in the playlist
	if (playlistData && playlistData.items) {
		playlistData.items.forEach((item) => {
			const url = item.url;
			const mediaType = itemCustomFormats.get(url) || (currentConfig.Descargar_video ? "video" : "audio");
			const globalFileFormat = mediaType === "audio" ? currentConfig.Formato_audio || "mp3" : currentConfig.Formato_video || "mp4";
			const fileFormat = itemFileFormats.get(url) || globalFileFormat;

			formats[url] = {
				format: mediaType,
				fileFormat: fileFormat,
			};
		});
	}

	return formats;
}

// ============================================
// SponsorBlock Info Fetching
// ============================================
async function fetchSponsorBlockForPlaylist(playlist) {
	const currentConfig = getCurrentFormConfig();

	// Only fetch if SponsorBlock is enabled
	if (!currentConfig.SponsorBlock_enabled) {
		return;
	}

	const categories = currentConfig.SponsorBlock_categories || [];
	if (categories.length === 0) {
		return;
	}

	// Fetch for each item with video_id
	for (const item of playlist.items) {
		if (!item.video_id) continue;

		try {
			const formData = new FormData();
			formData.append("video_id", item.video_id);
			formData.append("categories", JSON.stringify(categories));
			formData.append("duration", item.duracion_segundos || 0);
			formData.append("csrf_token", window.APP_DATA.csrfToken);

			const response = await fetch("/sponsorblock_info", {
				method: "POST",
				body: formData,
			});

			const data = await response.json();

			if (data.success && data.has_segments) {
				// Update badge
				const badge = document.querySelector(
					`.item-sb-badge-inline[data-video-id="${item.video_id}"], .item-sb-badge[data-video-id="${item.video_id}"]`,
				);
				if (badge) {
					badge.style.display = "inline-flex";
					const sbDuration = badge.querySelector(".sb-duration");
					if (sbDuration) {
						sbDuration.textContent = data.adjusted_duration_str;
					}
				}

				// Update duration in meta (show both)
				const durationSpan = document.querySelector(`.media-item[data-index="${playlist.items.indexOf(item)}"] .item-duration`);
				if (durationSpan) {
					const original = durationSpan.dataset.original;
					durationSpan.innerHTML = `<span class="text-decoration-line-through text-muted">${original}</span> → ${data.adjusted_duration_str}`;
				}
			}
		} catch (error) {
			console.error(`Error fetching SponsorBlock for ${item.video_id}:`, error);
		}
	}
}

// ============================================
// Select/Deselect All buttons
// ============================================
document.getElementById("selectAllBtn").addEventListener("click", function () {
	if (!playlistData) return;

	document.querySelectorAll(".media-item-checkbox").forEach((checkbox) => {
		checkbox.checked = true;
		checkbox.closest(".media-item").classList.add("selected");
	});

	selectedItems = new Set(playlistData.items.map((item) => item.url));
	updatePlaylistCounts();
	updateDownloadButton();
});

document.getElementById("selectNoneBtn").addEventListener("click", function () {
	document.querySelectorAll(".media-item-checkbox").forEach((checkbox) => {
		checkbox.checked = false;
		checkbox.closest(".media-item").classList.remove("selected");
	});

	selectedItems.clear();
	updatePlaylistCounts();
	updateDownloadButton();
});

// ============================================
// Bulk Actions Dropdown
// ============================================

function initBulkActionsDropdown() {
	const dropdown = document.getElementById("bulkActionsDropdown");
	if (!dropdown) return;

	const toggle = dropdown.querySelector(".custom-dropdown-toggle");
	const items = dropdown.querySelectorAll(".custom-dropdown-item");

	toggle?.addEventListener("click", function (e) {
		toggleCustomDropdown(e);
	});

	items.forEach((item) => {
		item.addEventListener("click", function (e) {
			e.preventDefault();
			e.stopPropagation();

			const action = this.dataset.action;
			const format = this.dataset.format;

			if (action === "audio") {
				bulkSetAudio();
			} else if (action === "video") {
				bulkSetVideo();
			} else if (action === "format" && format) {
				bulkSetFormat(format);
			}

			dropdown.classList.remove("active");
		});
	});
}

// Initialize bulk actions on page load
document.addEventListener("DOMContentLoaded", function () {
	initBulkActionsDropdown();
});

function bulkSetAudio() {
	if (!playlistData) return;
	playlistData.items.forEach((item) => {
		itemCustomFormats.set(item.url, "audio");

		// Update button states in unified controls
		const container = document.querySelector(`.media-item[data-url="${item.url}"] .unified-format-controls`);
		if (container) {
			const audioBtn = container.querySelector(".format-btn-audio");
			const videoBtn = container.querySelector(".format-btn-video");
			audioBtn?.classList.add("active");
			videoBtn?.classList.remove("active");
		}

		updateFileFormatDropdown(item.url, "audio");
	});
		showToast({ key: "toast.allItemsAudio" }, "success");
}

function bulkSetVideo() {
	if (!playlistData) return;
	playlistData.items.forEach((item) => {
		itemCustomFormats.set(item.url, "video");

		// Update button states in unified controls
		const container = document.querySelector(`.media-item[data-url="${item.url}"] .unified-format-controls`);
		if (container) {
			const audioBtn = container.querySelector(".format-btn-audio");
			const videoBtn = container.querySelector(".format-btn-video");
			videoBtn?.classList.add("active");
			audioBtn?.classList.remove("active");
		}

		updateFileFormatDropdown(item.url, "video");
	});
		showToast({ key: "toast.allItemsVideo" }, "success");
}

function bulkSetFormat(format) {
	if (!playlistData) return;

	// Determine if format is audio or video
	const audioFormats = ["mp3", "m4a", "flac", "wav"];
	const videoFormats = ["mp4", "mkv", "webm"];
	const isAudio = audioFormats.includes(format);
	const targetType = isAudio ? "audio" : "video";

	playlistData.items.forEach((item) => {
		// First, set the media type (audio/video) if needed
		const currentFormat = itemCustomFormats.get(item.url);
		if (currentFormat !== targetType) {
			itemCustomFormats.set(item.url, targetType);

			// Update button states in unified controls
			const container = document.querySelector(`.media-item[data-url="${item.url}"] .unified-format-controls`);
			if (container) {
				const audioBtn = container.querySelector(".format-btn-audio");
				const videoBtn = container.querySelector(".format-btn-video");
				if (isAudio) {
					audioBtn?.classList.add("active");
					videoBtn?.classList.remove("active");
				} else {
					videoBtn?.classList.add("active");
					audioBtn?.classList.remove("active");
				}
			}

			// Update dropdown options for the new type
			updateFileFormatDropdown(item.url, targetType);
		}

		// Then set the specific file format
		const dropdown = document.querySelector(`.custom-dropdown[data-url="${item.url}"]`);
		if (dropdown) {
			const valueSpan = dropdown.querySelector(".dropdown-value");
			valueSpan.textContent = format.toUpperCase();
			setItemFileFormat(item.url, format);
		}
	});
	showToast({ key: "toast.allItemsFileFormat", params: { format: format.toUpperCase() } }, "success");
}

// Close playlist selector
document.getElementById("closePlaylistBtn").addEventListener("click", function () {
	document.getElementById("playlistContainer").style.display = "none";
	playlistData = null;
	selectedItems.clear();
	itemCustomFormats.clear();
	itemFileFormats.clear();
	updateDownloadButton();
});

// ============================================
// Download Form — SSE Real-Time Progress
// ============================================
document.getElementById("downloadForm").addEventListener("submit", function (event) {
	event.preventDefault();

	// Mark download as started so beforeunload will warn the user
	if (window.OfflinerDownload && typeof window.OfflinerDownload.start === "function") {
		window.OfflinerDownload.start();
	}

	const downloadBtn = document.getElementById("downloadBtn");
	const downloadingBtn = document.getElementById("downloadingBtn");
	const progressContainer = document.getElementById("progressContainer");
	const progressBar = document.getElementById("progressBar");
	const progressStatus = document.getElementById("progressStatus");
	const progressPercentage = document.getElementById("progressPercentage");
	const progressDetail = document.getElementById("progressDetail");

	const inputURL = document.getElementById("inputURL").value.trim();
	const isPlaylistMode = document.getElementById("isPlaylistModeInput").value === "true";

	if (!inputURL && !isPlaylistMode) {
		showToast({ key: "toast.enterUrlOrSong" }, "warning");
		return;
	}

	if (isPlaylistMode && selectedItems.size === 0) {
		showToast({ key: "toast.selectOneItem" }, "warning");
		return;
	}

	// Update config before sending
	const currentConfig = getCurrentFormConfig();

	// Apply media preview format overrides for individual videos
	if (!isPlaylistMode && currentMediaInfo) {
		if (mediaPreviewFormat === "audio") {
			currentConfig.Descargar_audio = true;
			currentConfig.Descargar_video = false;
		} else if (mediaPreviewFormat === "video") {
			currentConfig.Descargar_audio = false;
			currentConfig.Descargar_video = true;
		}

		// Apply file format if selected
		if (mediaPreviewFileFormat) {
			const audioFormats = ["mp3", "m4a", "flac", "wav"];
			const videoFormats = ["mp4", "mkv", "webm", "mov"];

			if (audioFormats.includes(mediaPreviewFileFormat)) {
				currentConfig.Formato_audio = mediaPreviewFileFormat;
			} else if (videoFormats.includes(mediaPreviewFileFormat)) {
				currentConfig.Formato_video = mediaPreviewFileFormat;
			}
		}
	}

	document.getElementById("userConfigInput").value = JSON.stringify(currentConfig);

	// Add individual item configurations for playlist mode
	if (isPlaylistMode) {
		const itemConfigs = getItemCustomFormats();

		let itemConfigInput = document.getElementById("itemConfigsInput");
		if (!itemConfigInput) {
			itemConfigInput = document.createElement("input");
			itemConfigInput.type = "hidden";
			itemConfigInput.id = "itemConfigsInput";
			itemConfigInput.name = "item_configs";
			document.getElementById("downloadForm").appendChild(itemConfigInput);
		}
		itemConfigInput.value = JSON.stringify(itemConfigs);
	}

	// --- Show progress UI ---
	downloadBtn.style.display = "none";
	downloadingBtn.style.display = "block";
	progressContainer.style.display = "block";

	let lastPercent = 0;

	function updateProgress(percent, status, detail) {
		const pct = Math.max(percent || 0, lastPercent);
		lastPercent = pct;
		progressBar.style.width = pct + "%";
		progressPercentage.textContent = pct + "%";
		progressStatus.textContent = resolveI18nMessage(status) || "";
		progressDetail.textContent = resolveI18nMessage(detail) || "";
	}

	function resetUI() {
		setTimeout(() => {
			downloadBtn.style.display = "block";
			downloadingBtn.style.display = "none";
			progressContainer.style.display = "none";
			progressBar.style.width = "0%";
			progressBar.classList.remove("bg-success", "bg-danger");
			progressBar.classList.add("bg-primary", "progress-bar-animated");
			lastPercent = 0;
		}, 3000);
	}

	updateProgress(2, { key: "progress.starting" }, { key: "progress.sendingRequest" });

	const formData = new FormData(event.target);

	// Step 1: POST to /descargar — start download, receive request_id
	fetch(window.APP_DATA.urls.descargar, { method: "POST", body: formData })
		.then(async (response) => {
			let data;
			try {
				data = await response.json();
			} catch (e) {
				throw new Error(t("error.invalidServerResponse"));
			}
			if (!response.ok) {
				throw new Error(data.error || t("error.couldNotStartDownload"));
			}
			const requestId = data.request_id;
			if (!requestId) {
				throw new Error(t("error.noRequestId"));
			}

			updateProgress(5, { key: "progress.downloadStarted" }, { key: "progress.connectingStream" });

			// Step 2: Open SSE connection for real-time progress
			return new Promise((resolve, reject) => {
				let isCompleted = false;
				const evtSource = new EventSource(`/stream_progress/${requestId}`);

				evtSource.onmessage = function (event) {
					try {
						const progress = JSON.parse(event.data);

						// Build detail text with speed and ETA
						let detailText = progress.detail || "";
						if (progress.speed) detailText += ` \u2022 ${progress.speed}`;
						if (progress.eta) detailText += ` \u2022 ${t("progress.etaLabel")}: ${progress.eta}`;

						updateProgress(progress.percent, progress.status, detailText);

						if (progress.complete && !progress.error) {
							isCompleted = true;
							evtSource.close();
							updateProgress(100, { key: "progress.completed" }, { key: "progress.savingFile" });
							progressBar.classList.remove("progress-bar-animated");
							progressBar.classList.replace("bg-primary", "bg-success");

							// Use a direct anchor navigation to trigger the download.
							// fetch()+blob() competes with the SSE connection for the
							// browser's per-host connection pool, causing the request
							// to hang.  A native <a> click uses a separate download
							// channel that is not subject to this limitation.
							setTimeout(() => {
								const a = document.createElement("a");
								a.href = `/download_file/${requestId}`;
								a.download = "";
								a.style.display = "none";
								document.body.appendChild(a);
								a.click();
								a.remove();
								showToast({ key: "toast.downloadStarted" }, "success");
								resolve();
							}, 150);
						} else if (progress.error) {
							isCompleted = true;
							evtSource.close();
							reject(new Error(progress.error));
						}
					} catch (parseErr) {
						console.error("Error parsing SSE data:", parseErr);
					}
				};

				evtSource.onerror = function () {
					evtSource.close();
					if (!isCompleted) {
						reject(new Error(t("toast.lostProgressStream")));
					}
				};
			});
		})
		.then(() => {
			// Success — clean up UI inputs
			document.getElementById("inputURL").value = "";
			hideMediaPreview();
			if (playlistData) {
				document.getElementById("playlistContainer").style.display = "none";
				playlistData = null;
				selectedItems.clear();
				updateDownloadButton();
			}
		})
		.catch((error) => {
			progressBar.classList.remove("progress-bar-animated");
			progressBar.classList.replace("bg-primary", "bg-danger");
			const errMsg = error.message || t("toast.downloadError");
			updateProgress(100, { key: "progress.error" }, errMsg);
			showToast(errMsg, "danger");
			console.error("Download error:", error);
		})
		.finally(() => {
			if (window.OfflinerDownload && typeof window.OfflinerDownload.finish === "function") {
				window.OfflinerDownload.finish();
			}
			resetUI();
		});
});

// Inline behaviors moved from template (kept separate from main dashboard.js)
(function () {
	const userConfigInput = document.getElementById("userConfigInput");
	function parseConfig() {
		try {
			return JSON.parse(userConfigInput.value || "{}");
		} catch (e) {
			return {};
		}
	}
	function updateUserConfig() {
		const cfg = parseConfig();
		const sel = document.querySelector('input[name="download_type"]:checked');
		if (sel) cfg.download_type = sel.value;
		userConfigInput.value = JSON.stringify(cfg);
	}

	function refreshOptionCardSelection() {
		document.querySelectorAll(".option-card").forEach((card) => {
			const input = card.querySelector('input[type="radio"], input[type="checkbox"]');
			if (!input) return;
			if (input.type === "radio") {
				const groupChecked = document.querySelector('input[name="' + input.name + '"]:checked');
				if (groupChecked && card.contains(groupChecked)) card.classList.add("selected");
				else card.classList.remove("selected");
			} else {
				if (input.checked) card.classList.add("selected");
				else card.classList.remove("selected");
			}
		});
	}

	document.addEventListener("DOMContentLoaded", function () {
		// wire up change handlers for download_type and option-cards
		document.querySelectorAll('input[name="download_type"]').forEach((el) =>
			el.addEventListener("change", function (e) {
				updateUserConfig();
				refreshOptionCardSelection();
			}),
		);
		// wire up all inputs inside option-card to refresh selection classes
		document
			.querySelectorAll('.option-card input[type="radio"], .option-card input[type="checkbox"]')
			.forEach((i) => i.addEventListener("change", refreshOptionCardSelection));

		const saveBtn = document.getElementById("saveConfigBtn");
		if (saveBtn) saveBtn.addEventListener("click", updateUserConfig);

		// Prefer persisted user config; fall back to server-provided config, then defaults
		try {
			const loaded = getConfig();
			const v =
				loaded.download_type ?? loaded.Tipo_descarga ?? (window.APP_DATA && window.APP_DATA.config && window.APP_DATA.config.Tipo_descarga) ?? "audio";
			const el = document.getElementById("tipo_" + v);
			if (el) el.checked = true;
			else {
				const defaultEl = document.getElementById("tipo_audio");
				if (defaultEl) defaultEl.checked = true;
			}
		} catch (e) {
			const defaultEl = document.getElementById("tipo_audio");
			if (defaultEl) defaultEl.checked = true;
		}
		// ensure labels reflect current state
		refreshOptionCardSelection();
		updateUserConfig();
	});
})();

(function () {
	// Paste button handler: reads clipboard and pastes into inputURL
	async function pasteFromClipboard() {
		const input = document.getElementById("inputURL");
		if (!navigator.clipboard) {
			// Fallback: focus input and let user paste manually
			input.focus();
			return;
		}
		try {
			const text = await navigator.clipboard.readText();
			if (text) {
				input.value = text;
				// trigger input events in case other scripts listen
				input.dispatchEvent(new Event("input", { bubbles: true }));
			}
		} catch (e) {
			// ignore errors and focus input
			input.focus();
		}
	}

	document.addEventListener("DOMContentLoaded", function () {
		const pasteBtn = document.getElementById("pasteBtn");
		if (pasteBtn) pasteBtn.addEventListener("click", pasteFromClipboard);
	});
})();
