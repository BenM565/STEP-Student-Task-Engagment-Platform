/* ============================================
   STEP PLATFORM - COMMAND PALETTE (Phase 1)
   Global search with keyboard navigation
   ============================================ */

(function() {
    'use strict';

    /**
     * Command Palette Controller
     *
     * Provides global search functionality with:
     * - Keyboard shortcuts (Ctrl+K / ⌘K)
     * - Arrow key navigation
     * - Enter to navigate
     * - ESC to close
     * - Role-scoped results (students, companies, admins see different data)
     */
    class CommandPalette {
        constructor() {
            this.isOpen = false;
            this.selectedIndex = 0;
            this.results = [];
            this.debounceTimer = null;

            // DOM elements
            this.overlay = null;
            this.modal = null;
            this.input = null;
            this.resultsContainer = null;

            this.init();
        }

        /**
         * Initialize palette - create DOM and bind events
         */
        init() {
            this.createDOM();
            this.bindEvents();
            console.log('Command Palette: Initialized');
        }

        /**
         * Create palette DOM structure
         */
        createDOM() {
            // Create overlay
            this.overlay = document.createElement('div');
            this.overlay.id = 'command-palette-overlay';
            this.overlay.className = 'command-palette-overlay';

            // Create modal
            this.modal = document.createElement('div');
            this.modal.className = 'command-palette-modal';

            // Create search input
            const inputContainer = document.createElement('div');
            inputContainer.className = 'command-palette-input-container';

            const searchIcon = document.createElement('i');
            searchIcon.className = 'bi bi-search command-palette-icon';

            this.input = document.createElement('input');
            this.input.type = 'text';
            this.input.className = 'command-palette-input';
            this.input.placeholder = 'Search tasks, students, pages...';
            this.input.autocomplete = 'off';

            const kbd = document.createElement('kbd');
            kbd.className = 'command-palette-kbd';
            kbd.textContent = 'ESC';

            inputContainer.appendChild(searchIcon);
            inputContainer.appendChild(this.input);
            inputContainer.appendChild(kbd);

            // Create results container
            this.resultsContainer = document.createElement('div');
            this.resultsContainer.className = 'command-palette-results';

            // Assemble
            this.modal.appendChild(inputContainer);
            this.modal.appendChild(this.resultsContainer);
            this.overlay.appendChild(this.modal);

            document.body.appendChild(this.overlay);
        }

        /**
         * Bind keyboard and input events
         */
        bindEvents() {
            // Global keyboard shortcut (Ctrl+K / ⌘K)
            document.addEventListener('keydown', (e) => {
                // Ctrl+K or Cmd+K
                if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
                    e.preventDefault();
                    this.toggle();
                }

                // ESC to close
                if (e.key === 'Escape' && this.isOpen) {
                    this.close();
                }
            });

            // Click overlay to close
            this.overlay.addEventListener('click', (e) => {
                if (e.target === this.overlay) {
                    this.close();
                }
            });

            // Input events
            this.input.addEventListener('input', () => {
                this.handleInput();
            });

            // Keyboard navigation in results
            this.input.addEventListener('keydown', (e) => {
                if (!this.results.length) return;

                if (e.key === 'ArrowDown') {
                    e.preventDefault();
                    this.selectNext();
                } else if (e.key === 'ArrowUp') {
                    e.preventDefault();
                    this.selectPrevious();
                } else if (e.key === 'Enter') {
                    e.preventDefault();
                    this.navigateToSelected();
                }
            });
        }

        /**
         * Toggle palette open/close
         */
        toggle() {
            if (this.isOpen) {
                this.close();
            } else {
                this.open();
            }
        }

        /**
         * Open palette
         */
        open() {
            this.isOpen = true;
            this.overlay.classList.add('active');
            this.input.value = '';
            this.input.focus();
            this.resultsContainer.innerHTML = '';
            this.results = [];
            this.selectedIndex = 0;

            // Show helper text
            this.showHelper();
        }

        /**
         * Close palette
         */
        close() {
            this.isOpen = false;
            this.overlay.classList.remove('active');
            this.input.blur();
        }

        /**
         * Show helper text when palette is empty
         */
        showHelper() {
            this.resultsContainer.innerHTML = `
                <div class="command-palette-helper">
                    <i class="bi bi-lightbulb text-muted" style="font-size: 2rem; opacity: 0.5;"></i>
                    <p class="text-muted mb-1 mt-2">Start typing to search</p>
                    <div class="d-flex gap-2 justify-content-center mt-3">
                        <kbd>↑↓</kbd> <span class="text-muted small">Navigate</span>
                        <kbd>↵</kbd> <span class="text-muted small">Open</span>
                        <kbd>ESC</kbd> <span class="text-muted small">Close</span>
                    </div>
                </div>
            `;
        }

        /**
         * Handle input changes with debouncing
         */
        handleInput() {
            const query = this.input.value.trim();

            if (query.length < 2) {
                this.showHelper();
                this.results = [];
                this.selectedIndex = 0;
                return;
            }

            // Debounce search requests
            clearTimeout(this.debounceTimer);
            this.debounceTimer = setTimeout(() => {
                this.search(query);
            }, 200);
        }

        /**
         * Perform search via API
         *
         * @param {string} query - Search term
         */
        async search(query) {
            try {
                const response = await fetch(`/api/search?q=${encodeURIComponent(query)}`);

                if (!response.ok) {
                    throw new Error('Search failed');
                }

                const data = await response.json();
                this.displayResults(data);
            } catch (error) {
                console.error('Command Palette: Search error', error);
                this.resultsContainer.innerHTML = `
                    <div class="command-palette-error">
                        <i class="bi bi-exclamation-triangle text-danger"></i>
                        <p class="text-danger mt-2 mb-0">Search failed. Please try again.</p>
                    </div>
                `;
            }
        }

        /**
         * Display search results
         *
         * @param {Object} data - API response with tasks, students, companies, pages
         */
        displayResults(data) {
            this.results = [];
            this.selectedIndex = 0;
            this.resultsContainer.innerHTML = '';

            const hasResults = Object.values(data).some(arr => arr.length > 0);

            if (!hasResults) {
                this.resultsContainer.innerHTML = `
                    <div class="command-palette-empty">
                        <i class="bi bi-inbox text-muted" style="font-size: 2rem; opacity: 0.5;"></i>
                        <p class="text-muted mt-2 mb-0">No results found</p>
                    </div>
                `;
                return;
            }

            // Render each category
            if (data.pages && data.pages.length > 0) {
                this.renderCategory('Pages', data.pages, 'bi-file-text', 'page');
            }

            if (data.tasks && data.tasks.length > 0) {
                this.renderCategory('Tasks', data.tasks, 'bi-briefcase', 'task');
            }

            if (data.students && data.students.length > 0) {
                this.renderCategory('Students', data.students, 'bi-person', 'student');
            }

            if (data.companies && data.companies.length > 0) {
                this.renderCategory('Companies', data.companies, 'bi-building', 'company');
            }

            // Select first result
            if (this.results.length > 0) {
                this.updateSelection();
            }
        }

        /**
         * Render a category of results
         *
         * @param {string} title - Category title
         * @param {Array} items - Result items
         * @param {string} icon - Bootstrap icon class
         * @param {string} type - Result type
         */
        renderCategory(title, items, icon, type) {
            const section = document.createElement('div');
            section.className = 'command-palette-section';

            const header = document.createElement('div');
            header.className = 'command-palette-section-title';
            header.textContent = title;

            section.appendChild(header);

            items.forEach(item => {
                const resultItem = document.createElement('div');
                resultItem.className = 'command-palette-item';
                resultItem.dataset.url = item.url;

                const iconEl = document.createElement('i');
                iconEl.className = `bi ${icon} command-palette-item-icon`;

                const content = document.createElement('div');
                content.className = 'command-palette-item-content';

                const titleEl = document.createElement('div');
                titleEl.className = 'command-palette-item-title';
                titleEl.textContent = item.title || item.name;

                content.appendChild(titleEl);

                // Add subtitle for tasks (company name) and students/companies (email)
                if (type === 'task' && item.company_name) {
                    const subtitle = document.createElement('div');
                    subtitle.className = 'command-palette-item-subtitle';
                    subtitle.textContent = item.company_name;
                    content.appendChild(subtitle);
                } else if ((type === 'student' || type === 'company') && item.email) {
                    const subtitle = document.createElement('div');
                    subtitle.className = 'command-palette-item-subtitle';
                    subtitle.textContent = item.email;
                    content.appendChild(subtitle);
                }

                resultItem.appendChild(iconEl);
                resultItem.appendChild(content);

                // Click to navigate
                resultItem.addEventListener('click', () => {
                    window.location.href = item.url;
                });

                section.appendChild(resultItem);
                this.results.push(resultItem);
            });

            this.resultsContainer.appendChild(section);
        }

        /**
         * Select next item in results
         */
        selectNext() {
            if (this.selectedIndex < this.results.length - 1) {
                this.selectedIndex++;
                this.updateSelection();
            }
        }

        /**
         * Select previous item in results
         */
        selectPrevious() {
            if (this.selectedIndex > 0) {
                this.selectedIndex--;
                this.updateSelection();
            }
        }

        /**
         * Update visual selection
         */
        updateSelection() {
            // Remove previous selection
            this.results.forEach(item => item.classList.remove('selected'));

            // Add selection to current item
            if (this.results[this.selectedIndex]) {
                const selected = this.results[this.selectedIndex];
                selected.classList.add('selected');

                // Scroll into view if needed
                selected.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
            }
        }

        /**
         * Navigate to selected result
         */
        navigateToSelected() {
            if (this.results[this.selectedIndex]) {
                const url = this.results[this.selectedIndex].dataset.url;
                if (url) {
                    window.location.href = url;
                }
            }
        }
    }

    // Initialize on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            new CommandPalette();
        });
    } else {
        new CommandPalette();
    }

})();
