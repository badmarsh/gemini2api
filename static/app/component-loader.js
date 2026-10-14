/**
 * Component Loader - For dynamically loading HTML component fragments
 */

// Component cache
const componentCache = new Map();

/**
 * Load a single component
 * @param {string} componentPath - Component file path
 * @returns {Promise<string>} - Component HTML content
 */
async function loadComponent(componentPath) {
    if (componentCache.has(componentPath)) {
        return componentCache.get(componentPath);
    }

    try {
        const response = await fetch(componentPath);
        if (!response.ok) {
            throw new Error(`Failed to load component: ${componentPath} (${response.status})`);
        }
        const html = await response.text();
        componentCache.set(componentPath, html);
        return html;
    } catch (error) {
        console.error(`Error loading component ${componentPath}:`, error);
        throw error;
    }
}

/**
 * Insert component into specified container
 * @param {string} componentPath - Component file path
 * @param {string|HTMLElement} container - Container selector or element
 * @param {string} position - Insertion position: 'replace', 'append', 'prepend', 'beforeend', 'afterbegin'
 * @returns {Promise<void>}
 */
async function insertComponent(componentPath, container, position = 'beforeend') {
    const html = await loadComponent(componentPath);

    const containerElement = typeof container === 'string'
        ? document.querySelector(container)
        : container;

    if (!containerElement) {
        throw new Error(`Container not found: ${container}`);
    }

    if (position === 'replace') {
        containerElement.innerHTML = html;
    } else {
        containerElement.insertAdjacentHTML(position, html);
    }
}

/**
 * Batch load multiple components
 * @param {Array<{path: string, container: string, position?: string}>} components - Component configs
 * @returns {Promise<void>}
 */
async function loadComponents(components) {
    const promises = components.map(({ path, container, position }) =>
        insertComponent(path, container, position)
    );
    await Promise.all(promises);
}

/**
 * Initialize page components
 * Loads all page components and inserts them into appropriate layout slots
 * @returns {Promise<void>}
 */
async function initializeComponents() {
    const basePath = 'components/';

    try {
        // Load header first
        await insertComponent(`${basePath}header.html`, '.container', 'afterbegin');

        // Load sidebar next
        await insertComponent(`${basePath}sidebar.html`, '#sidebar-container', 'replace');

        // Load all section components
        const sectionComponents = [
            { path: `${basePath}section-dashboard.html`, container: '#content-container', position: 'beforeend' },
            { path: `${basePath}section-accounts.html`, container: '#content-container', position: 'beforeend' },
            { path: `${basePath}section-playground.html`, container: '#content-container', position: 'beforeend' },
            { path: `${basePath}section-usage-stats.html`, container: '#content-container', position: 'beforeend' },
            { path: `${basePath}section-logs.html`, container: '#content-container', position: 'beforeend' },
            { path: `${basePath}section-api-keys.html`, container: '#content-container', position: 'beforeend' },
            { path: `${basePath}section-gems.html`, container: '#content-container', position: 'beforeend' },
            { path: `${basePath}section-settings.html`, container: '#content-container', position: 'beforeend' },
        ];

        await loadComponents(sectionComponents);

        console.log('All components loaded successfully');
        window.dispatchEvent(new CustomEvent('componentsLoaded'));

    } catch (error) {
        console.error('Failed to initialize components:', error);
        throw error;
    }
}

/**
 * Clear component cache
 */
function clearComponentCache() {
    componentCache.clear();
}

// Export functions
export {
    loadComponent,
    insertComponent,
    loadComponents,
    initializeComponents,
    clearComponentCache
};
