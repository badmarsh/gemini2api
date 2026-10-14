/**
 * Theme switcher module
 * Supports light / dark theme switching and persists preference via safeStorage.
 */

import { safeStorage } from './services/storageService.js';
import { APP_CONSTANTS } from './config/constants.js';

// Theme constants
const THEME_KEY = APP_CONSTANTS.STORAGE_KEYS.THEME;
const THEME_LIGHT = 'light';
const THEME_DARK = 'dark';

/**
 * Get current theme
 * @returns {string} Current theme ('light' or 'dark')
 */
function getCurrentTheme() {
    const savedTheme = safeStorage.getItem(THEME_KEY);
    if (savedTheme) {
        return savedTheme;
    }

    if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
        return THEME_DARK;
    }

    return THEME_LIGHT;
}

/**
 * Set theme
 * @param {string} theme - Theme name ('light' or 'dark')
 */
function setTheme(theme) {
    const root = document.documentElement;

    if (theme === THEME_DARK) {
        root.setAttribute('data-theme', THEME_DARK);
    } else {
        root.removeAttribute('data-theme');
    }

    safeStorage.setItem(THEME_KEY, theme);
    updateMetaThemeColor(theme);
    window.dispatchEvent(new CustomEvent('themechange', { detail: { theme } }));
}

/**
 * Toggle theme
 * @returns {string} Switched theme
 */
function toggleTheme() {
    const currentTheme = getCurrentTheme();
    const newTheme = currentTheme === THEME_DARK ? THEME_LIGHT : THEME_DARK;
    setTheme(newTheme);
    return newTheme;
}

/**
 * Update meta theme-color
 * @param {string} theme - Theme name
 */
function updateMetaThemeColor(theme) {
    const metaThemeColor = document.querySelector('meta[name="theme-color"]');
    if (metaThemeColor) {
        metaThemeColor.setAttribute('content', theme === THEME_DARK ? '#1f2937' : '#4285f4');
    }
}

/**
 * Initialize theme switcher
 * @param {string} [toggleButtonId='themeToggleBtn'] - Toggle button ID
 */
function initThemeSwitcher(toggleButtonId = 'themeToggleBtn') {
    const savedTheme = getCurrentTheme();
    setTheme(savedTheme);

    const toggleBtn = document.getElementById(toggleButtonId);
    if (toggleBtn) {
        toggleBtn.addEventListener('click', () => {
            toggleTheme();
        });
    }

    if (window.matchMedia) {
        const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
        mediaQuery.addEventListener('change', (e) => {
            const savedTheme = safeStorage.getItem(THEME_KEY);
            if (!savedTheme) {
                setTheme(e.matches ? THEME_DARK : THEME_LIGHT);
            }
        });
    }
}

/**
 * Check if dark theme is active
 * @returns {boolean}
 */
function isDarkMode() {
    return getCurrentTheme() === THEME_DARK;
}

export {
    THEME_KEY,
    THEME_LIGHT,
    THEME_DARK,
    getCurrentTheme,
    setTheme,
    toggleTheme,
    initThemeSwitcher,
    isDarkMode
};
