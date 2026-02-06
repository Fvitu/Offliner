// ============================================
// Configuración por defecto del servidor
// ============================================
const DEFAULT_CONFIG = window.APP_DATA.config;
const STORAGE_KEY = "music_downloader_config";

// ============================================
// Funciones de localStorage para configuración
// ============================================
function getConfig() {
	try {
		const stored = localStorage.getItem(STORAGE_KEY);
		if (stored) {
			const parsed = JSON.parse(stored);
			// Merge con defaults para asegurar que existan todas las claves
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

	// Checkboxes (excepto SponsorBlock categories)
	document.querySelectorAll('#configForm input[type="checkbox"]:not(.sponsorblock-category)').forEach((checkbox) => {
		config[checkbox.name] = checkbox.checked;
	});

	// SponsorBlock categories
	const categories = [];
	document.querySelectorAll(".sponsorblock-category:checked").forEach((checkbox) => {
		categories.push(checkbox.value);
	});
	config["SponsorBlock_categories"] = categories;

	// Auto-detectar fuente desde la URL actual
	const inputURL = document.getElementById("inputURL")?.value?.trim() || "";
	const fuente = detectarFuente(inputURL);
	if (fuente === "spotify") {
		config["Fuente_descarga"] = "Spotify";
	} else {
		config["Fuente_descarga"] = "YouTube";
	}

	return config;
}

function applyConfigToForm(config) {
	// Radio buttons (sin Fuente_descarga - ahora se auto-detecta)
	["Calidad_audio_video", "Formato_audio", "Formato_video"].forEach((name) => {
		// Primero remover 'selected' de todos los cards del mismo grupo
		document.querySelectorAll(`input[name="${name}"]`).forEach((r) => {
			r.closest(".option-card")?.classList.remove("selected");
		});

		const value = config[name];
		const radio = document.querySelector(`input[name="${name}"][value="${value}"]`);
		if (radio) {
			radio.checked = true;
			radio.closest(".option-card")?.classList.add("selected");
		}
	});

	// Checkboxes
	["Descargar_audio", "Descargar_video", "Scrappear_metadata", "Preferir_YouTube_Music", "SponsorBlock_enabled"].forEach((name) => {
		const checkbox = document.querySelector(`input[name="${name}"]`);
		if (checkbox) {
			checkbox.checked = !!config[name];
			checkbox.closest(".option-card")?.classList.toggle("selected", checkbox.checked);
		}
	});

	// SponsorBlock categories
	if (config.SponsorBlock_categories && Array.isArray(config.SponsorBlock_categories)) {
		config.SponsorBlock_categories.forEach((cat) => {
			const checkbox = document.querySelector(`.sponsorblock-category[value="${cat}"]`);
			if (checkbox) {
				checkbox.checked = true;
				checkbox.closest(".option-card")?.classList.add("selected");
			}
		});
	}

	// Toggle SponsorBlock categories visibility
	toggleSponsorBlockCategories();
}

// ============================================
// Auto-detección de fuente desde URL
// ============================================
function detectarFuente(url) {
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

function esURLValida(url) {
	return detectarFuente(url) !== null;
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

	return `
		<div class="unified-format-controls">
			<div class="btn-group btn-group-sm" role="group">
				<button type="button" class="btn btn-outline-secondary format-btn-audio ${currentFormat === "audio" ? "active" : ""}" 
						data-url="${url}" onclick="setItemFormatType(event, '${url}', 'audio')">
					<i class="fa-solid fa-music"></i> Audio
				</button>
				<button type="button" class="btn btn-outline-secondary format-btn-video ${currentFormat === "video" ? "active" : ""}" 
						data-url="${url}" onclick="setItemFormatType(event, '${url}', 'video')">
					<i class="fa-solid fa-video"></i> Video
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
	showToast(`Format set to ${formatText}`, "success");
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

	// Ocultar loading y mostrar contenido
	loading.style.display = "none";
	content.style.display = "flex";

	// Actualizar información
	document.getElementById("mediaThumbnail").src = info.thumbnail || "https://via.placeholder.com/120x90?text=No+Image";
	document.getElementById("mediaTitle").textContent = info.titulo || "Untitled";
	document.getElementById("mediaAuthor").textContent = info.autor || "Unknown";
	document.getElementById("mediaDuration").textContent = info.duracion || "0:00";

	// Actualizar badge de fuente
	const currentConfig = getCurrentFormConfig();
	const sourceBadge = document.getElementById("mediaSource");

	// Determinar el texto y el icono según la configuración y la fuente
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

	// Mostrar preview con animación
	preview.classList.add("visible");
	currentMediaInfo = info;

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
			showToast(`SponsorBlock: ${data.segment_count} segment(s) will be removed`, "info");
		}
	} catch (error) {
		console.error("Error fetching SponsorBlock for media preview:", error);
	}
}

function hideMediaPreview() {
	const preview = document.getElementById("mediaPreview");
	preview.classList.remove("visible");
	currentMediaInfo = null;
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

// Cerrar media preview
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

function showToast(message, type = "primary") {
	const container = document.getElementById("toastContainer");
	const toastId = "toast-" + Date.now() + "-" + Math.random().toString(36).slice(2, 6);

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
					${message}
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
	// Cargar configuración guardada
	const savedConfig = getConfig();
	applyConfigToForm(savedConfig);

	// Actualizar el input hidden con la configuración
	document.getElementById("userConfigInput").value = JSON.stringify(savedConfig);

	// ============================================
	// Config Form - Guardar en localStorage
	// ============================================
	document.getElementById("saveConfigBtn").addEventListener("click", function (e) {
		e.preventDefault();

		const btn = this;
		const originalContent = btn.innerHTML;

		// Show loading state
		btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>Saving...';
		btn.disabled = true;

		const config = getCurrentFormConfig();

		setTimeout(() => {
			if (saveConfig(config)) {
				showToast("Settings saved to your browser!", "success");
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
				showToast("Error saving settings", "danger");
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

// Función para verificar si es una URL de playlist
function isPlaylistURL(url) {
	if (!url) return false;
	const urlLower = url.toLowerCase();
	return (
		urlLower.includes("list=") ||
		urlLower.includes("/playlist") ||
		(urlLower.includes("spotify.com") && (urlLower.includes("/playlist/") || urlLower.includes("/album/")))
	);
}

// Detección automática de URL al escribir
document.getElementById("inputURL").addEventListener("input", function () {
	// Si ya estamos procesando un paste, ignorar
	if (isProcessingInput) return;

	const url = this.value.trim();

	// Cancelar timeout anterior si existe
	if (playlistCheckTimeout) {
		clearTimeout(playlistCheckTimeout);
	}
	if (mediaPreviewTimeout) {
		clearTimeout(mediaPreviewTimeout);
	}

	// Si el campo está vacío, ocultar todo
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

	// Si no es una URL válida, ocultar previews
	if (!esURLValida(url)) {
		hideMediaPreview();
		if (playlistData) {
			document.getElementById("playlistContainer").style.display = "none";
			playlistData = null;
			selectedItems.clear();
			updateDownloadButton();
		}
		return;
	}

	// Si es una URL de playlist
	if (isPlaylistURL(url)) {
		hideMediaPreview();
		playlistCheckTimeout = setTimeout(() => {
			checkForPlaylist();
		}, 800);
	} else {
		// Es una URL individual - mostrar preview
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

// También verificar al pegar una URL
document.getElementById("inputURL").addEventListener("paste", function () {
	// Marcar que estamos procesando para evitar duplicados
	isProcessingInput = true;

	// Cancelar timeouts anteriores
	if (playlistCheckTimeout) clearTimeout(playlistCheckTimeout);
	if (mediaPreviewTimeout) clearTimeout(mediaPreviewTimeout);

	setTimeout(async () => {
		const url = this.value.trim();
		if (!url) {
			isProcessingInput = false;
			return;
		}

		if (!esURLValida(url)) {
			hideMediaPreview();
			isProcessingInput = false;
			return;
		}

		if (isPlaylistURL(url)) {
			hideMediaPreview();
			await checkForPlaylist();
		} else {
			// URL individual - obtener info
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
		showToast("Please enter a URL or search term", "warning");
		return;
	}

	// Si es URL válida, usar el flujo existente
	if (esURLValida(query)) {
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

	container.style.display = "block";
	loading.style.display = "block";
	itemsContainer.style.display = "none";
	document.getElementById("playlistTitle").textContent = `Searching: ${query}...`;
	document.getElementById("playlistMeta").textContent = "Searching YouTube...";
	document.getElementById("playlistThumbnail").style.display = "none";

	try {
		const formData = new FormData();
		formData.append("query", query);
		formData.append("csrf_token", window.APP_DATA.csrfToken);

		// Check if YTM preference is enabled
		const preferYTM = document.querySelector('input[name="Preferir_YouTube_Music"]')?.checked;
		formData.append("prefer_ytmusic", preferYTM ? "true" : "false");

		const response = await fetch(window.APP_DATA.urls.search_youtube, {
			method: "POST",
			body: formData,
		});

		const data = await response.json();

		if (data.success && data.playlist) {
			showPlaylistSelector(data.playlist);
		} else {
			container.style.display = "none";
			showToast(data.error || "No results found", "warning");
		}
	} catch (error) {
		console.error("Search error:", error);
		container.style.display = "none";
		showToast("Error performing search", "danger");
	}
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

	// Mostrar loading
	container.style.display = "block";
	loading.style.display = "block";
	itemsContainer.style.display = "none";
	document.getElementById("playlistTitle").textContent = "Loading playlist...";
	document.getElementById("playlistMeta").textContent = "Getting info...";
	document.getElementById("playlistThumbnail").style.display = "none";

	try {
		const formData = new FormData();
		formData.append("url", url);
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
			showToast("This URL is not a playlist. You can download it directly.", "info");
		} else {
			container.style.display = "none";
			showToast(data.error || "Could not get playlist", "danger");
		}
	} catch (error) {
		console.error("Error:", error);
		container.style.display = "none";
		showToast("Error checking playlist", "danger");
	}
}

function showPlaylistSelector(playlist) {
	playlistData = playlist;
	selectedItems = new Set(playlist.items.map((item) => item.url)); // Select all by default

	const container = document.getElementById("playlistContainer");
	const loading = document.getElementById("playlistLoading");
	const itemsContainer = document.getElementById("playlistItemsContainer");

	// Update header info
	document.getElementById("playlistTitle").textContent = playlist.titulo;
	document.getElementById("playlistMeta").textContent = `${playlist.total} videos • ${playlist.autor}`;

	if (playlist.thumbnail) {
		const thumb = document.getElementById("playlistThumbnail");
		thumb.src = playlist.thumbnail;
		thumb.style.display = "block";
	}

	// Show container with loading
	container.style.display = "block";
	loading.style.display = "block";
	itemsContainer.style.display = "none";

	// Build items HTML
	let html = "";
	playlist.items.forEach((item, index) => {
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
            <div class="media-item selected" data-url="${item.url}" data-duration="${item.duracion_segundos}" data-index="${index}">
                <span class="badge bg-secondary media-item-number">${index + 1}</span>
                <input type="checkbox" class="form-check-input media-item-checkbox" 
                       checked data-url="${item.url}" 
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
                        <span>${item.autor || "Unknown"}</span>
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
		loading.style.display = "none";
		itemsContainer.innerHTML = html;
		itemsContainer.style.display = "block";
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
	const btn = document.getElementById("downloadBtnText");
	const isPlaylistMode = playlistData !== null && selectedItems.size > 0;

	if (isPlaylistMode) {
		btn.textContent = `Download ${selectedItems.size} item(s)`;
	} else {
		btn.textContent = "Start Download";
	}

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
	showToast("All items set to AUDIO format", "success");
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
	showToast("All items set to VIDEO format", "success");
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
	showToast(`All items set to ${format.toUpperCase()} file format`, "success");
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
// Download Form
// ============================================
document.getElementById("downloadForm").addEventListener("submit", function (event) {
	event.preventDefault();

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
		showToast("Please enter a URL or song name", "warning");
		return;
	}

	if (isPlaylistMode && selectedItems.size === 0) {
		showToast("Select at least one playlist item", "warning");
		return;
	}

	// Actualizar configuración antes de enviar
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

		// Create or update hidden input for item configs
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

	downloadBtn.style.display = "none";
	downloadingBtn.style.display = "block";
	progressContainer.style.display = "block";

	const progressSteps = [
		{ percent: 5, status: "Starting...", detail: "Connecting to server" },
		{ percent: 15, status: "Searching...", detail: "Analyzing URL or search term" },
		{ percent: 25, status: "Found", detail: "Preparing download" },
		{ percent: 40, status: "Downloading...", detail: "Getting audio stream" },
		{ percent: 55, status: "Downloading...", detail: "Getting video stream" },
		{ percent: 70, status: "Processing...", detail: "Converting format" },
		{ percent: 85, status: "Finishing...", detail: "Adding metadata" },
		{ percent: 95, status: "Almost done...", detail: "Preparing file" },
	];

	let currentStep = 0;
	const startTime = Date.now();

	function updateProgress(percent, status, detail) {
		progressBar.style.width = percent + "%";
		progressPercentage.textContent = percent + "%";
		progressStatus.textContent = status;
		progressDetail.textContent = detail || "";
	}

	updateProgress(2, "Starting...", "Sending request");

	const progressInterval = setInterval(() => {
		const elapsed = (Date.now() - startTime) / 1000;
		let target = Math.min(Math.floor(elapsed / 3), progressSteps.length - 1);

		if (currentStep < target) {
			currentStep = target;
			const step = progressSteps[currentStep];
			updateProgress(step.percent, step.status, step.detail);
		}
	}, 500);

	const formData = new FormData(event.target);

	fetch(window.APP_DATA.urls.descargar, { method: "POST", body: formData })
		.then(async (response) => {
			clearInterval(progressInterval);

			if (!response.ok) {
				// Intentar obtener mensaje de error del servidor
				let errorMsg = "Could not complete download";
				try {
					const errorData = await response.json();
					if (errorData.error) {
						errorMsg = errorData.error;
					}
				} catch (e) {
					// No es JSON, usar mensaje genérico
				}
				updateProgress(100, "Error", errorMsg);
				progressBar.classList.replace("bg-primary", "bg-danger");
				throw new Error(errorMsg);
			}

			updateProgress(100, "Completed!", "Saving file...");
			progressBar.classList.remove("progress-bar-animated");
			progressBar.classList.replace("bg-primary", "bg-success");

			const disposition = response.headers.get("Content-Disposition");
			let filename = "download.mp3";

			if (disposition && disposition.includes("filename=")) {
				filename = disposition.split("filename=")[1].split(";")[0].trim().replace(/"/g, "");
				if (filename.includes("\\")) filename = filename.substring(filename.lastIndexOf("\\") + 1);
				if (filename.includes("/")) filename = filename.substring(filename.lastIndexOf("/") + 1);
			}

			return response.blob().then((blob) => ({ blob, filename }));
		})
		.then(({ blob, filename }) => {
			const url = window.URL.createObjectURL(blob);
			const a = document.createElement("a");
			a.href = url;
			a.download = filename;
			document.body.appendChild(a);
			a.click();
			window.URL.revokeObjectURL(url);
			a.remove();

			showToast("Download completed: " + filename, "success");

			// Limpiar el input URL después de descarga exitosa
			document.getElementById("inputURL").value = "";
			hideMediaPreview();

			// Close playlist selector after successful download
			if (playlistData) {
				document.getElementById("playlistContainer").style.display = "none";
				playlistData = null;
				selectedItems.clear();
				updateDownloadButton();
			}
		})
		.catch((error) => {
			clearInterval(progressInterval);
			progressBar.classList.remove("progress-bar-animated");
			progressBar.classList.replace("bg-primary", "bg-danger");
			const errorMsg = error.message || "Download error. Please try again.";
			showToast(errorMsg, "danger");
			console.error("Download error:", error);
		})
		.finally(() => {
			setTimeout(() => {
				downloadBtn.style.display = "block";
				downloadingBtn.style.display = "none";
				progressContainer.style.display = "none";
				progressBar.style.width = "0%";
				progressBar.classList.remove("bg-success", "bg-danger");
				progressBar.classList.add("bg-primary", "progress-bar-animated");
			}, 3000);
		});
});
