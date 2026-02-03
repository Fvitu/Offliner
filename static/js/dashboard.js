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
	const sourceBadge = document.getElementById("mediaSource");
	sourceBadge.className = "source-badge " + (info.fuente || "youtube");

	const sourceIcons = {
		youtube: '<i class="fa-brands fa-youtube"></i> YouTube',
		youtube_music: '<i class="fa-brands fa-youtube"></i> YouTube Music',
		spotify: '<i class="fa-brands fa-spotify"></i> Spotify',
	};
	sourceBadge.innerHTML = sourceIcons[info.fuente] || sourceIcons.youtube;

	// Mostrar preview con animación
	preview.classList.add("visible");
	currentMediaInfo = info;
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
// Toast System
// ============================================
function showToast(message, type = "primary") {
	const container = document.getElementById("toastContainer");
	const toastId = "toast-" + Date.now();

	const icons = {
		primary: "fa-circle-info",
		success: "fa-circle-check",
		danger: "fa-circle-exclamation",
		warning: "fa-triangle-exclamation",
	};

	const toastHtml = `
        <div id="${toastId}" class="toast toast-custom align-items-center text-bg-${type} border-0 show" role="alert">
            <div class="d-flex">
                <div class="toast-body">
                    <i class="fa-solid ${icons[type] || icons.primary} me-2"></i>
                    ${message}
                </div>
                <button type="button" class="btn-close btn-close-white me-2 m-auto" onclick="closeToast('${toastId}')"></button>
            </div>
        </div>
    `;

	container.insertAdjacentHTML("beforeend", toastHtml);

	// Auto-hide after 5 seconds
	setTimeout(() => closeToast(toastId), 5000);
}

function closeToast(toastId) {
	const toast = document.getElementById(toastId);
	if (toast) {
		toast.classList.add("toast-fade-out");
		setTimeout(() => toast.remove(), 500);
	}
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

				// Cerrar el offcanvas automáticamente
				// Usar el ID real del offcanvas
				const offcanvasElement = document.getElementById("configSidebar");
				let offcanvasInstance = bootstrap.Offcanvas.getInstance(offcanvasElement);
				if (!offcanvasInstance) {
					offcanvasInstance = new bootstrap.Offcanvas(offcanvasElement);
				}
				offcanvasInstance.hide();
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

	categories.style.display = checkbox.checked ? "block" : "none";
	card.classList.toggle("selected", checkbox.checked);
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
	return urlLower.includes("list=") || urlLower.includes("/playlist") || (urlLower.includes("spotify.com") && urlLower.includes("/playlist/"));
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
		html += `
            <div class="playlist-item selected" data-url="${item.url}" data-duration="${item.duracion_segundos}">
                <input type="checkbox" class="form-check-input playlist-item-checkbox" 
                       checked data-url="${item.url}" 
                       onchange="togglePlaylistItem(this)">
                <img src="${item.thumbnail || "https://via.placeholder.com/60x45?text=No+Image"}" 
                     alt="${item.titulo}" class="playlist-item-thumbnail"
                     onerror="this.src='https://via.placeholder.com/60x45?text=No+Image'">
                <div class="playlist-item-info">
                    <div class="playlist-item-title" title="${item.titulo}">${item.titulo}</div>
                    <div class="playlist-item-meta">
                        <i class="fa-solid fa-clock me-1"></i>${item.duracion}
                        ${item.autor ? `• ${item.autor}` : ""}
                    </div>
                </div>
                <div class="playlist-item-actions">
                    <span class="badge bg-secondary">${index + 1}</span>
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
	}, 500);
}

function togglePlaylistItem(checkbox) {
	const url = checkbox.dataset.url;
	const item = checkbox.closest(".playlist-item");

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

// Select/Deselect All buttons
document.getElementById("selectAllBtn").addEventListener("click", function () {
	if (!playlistData) return;

	document.querySelectorAll(".playlist-item-checkbox").forEach((checkbox) => {
		checkbox.checked = true;
		checkbox.closest(".playlist-item").classList.add("selected");
	});

	selectedItems = new Set(playlistData.items.map((item) => item.url));
	updatePlaylistCounts();
	updateDownloadButton();
});

document.getElementById("selectNoneBtn").addEventListener("click", function () {
	document.querySelectorAll(".playlist-item-checkbox").forEach((checkbox) => {
		checkbox.checked = false;
		checkbox.closest(".playlist-item").classList.remove("selected");
	});

	selectedItems.clear();
	updatePlaylistCounts();
	updateDownloadButton();
});

// Close playlist selector
document.getElementById("closePlaylistBtn").addEventListener("click", function () {
	document.getElementById("playlistContainer").style.display = "none";
	playlistData = null;
	selectedItems.clear();
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
	document.getElementById("userConfigInput").value = JSON.stringify(currentConfig);

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
