/* ============================================
   STEP PLATFORM - UI ENHANCEMENTS
   Modern animated backgrounds and interactions
   ============================================ */

(function() {
    'use strict';

    /* ============================================
       1. ANIMATED BACKGROUND SYSTEM
       ============================================ */

    /**
     * Initialize animated gradient mesh background
     * Only activates on pages with data-animated-bg attribute
     * GPU-friendly: uses transform and opacity only
     * Respects prefers-reduced-motion
     */
    function initAnimatedBackground() {
        // Check if page should have animated background
        const hasAnimatedBg = document.body.hasAttribute('data-animated-bg');
        if (!hasAnimatedBg) return;

        // Check user's motion preference
        const prefersReducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

        // Create background container
        const bgContainer = document.createElement('div');
        bgContainer.className = 'animated-bg-canvas';
        document.body.insertBefore(bgContainer, document.body.firstChild);

        // Skip canvas particles if user prefers reduced motion
        if (prefersReducedMotion) {
            console.log('Animated background: Reduced motion mode - canvas disabled');
            return;
        }

        // Create canvas for particle effects
        const canvas = document.createElement('canvas');
        canvas.id = 'bg-canvas';
        bgContainer.appendChild(canvas);

        const ctx = canvas.getContext('2d');
        let particles = [];
        let animationId;

        // Resize canvas to fill container
        function resizeCanvas() {
            canvas.width = window.innerWidth;
            canvas.height = window.innerHeight;
        }

        resizeCanvas();
        window.addEventListener('resize', resizeCanvas);

        /* ============================================
           Particle System
           ============================================ */

        class Particle {
            constructor() {
                this.reset();
            }

            reset() {
                this.x = Math.random() * canvas.width;
                this.y = Math.random() * canvas.height;
                this.size = Math.random() * 3 + 1;
                this.speedX = (Math.random() - 0.5) * 0.5;
                this.speedY = (Math.random() - 0.5) * 0.5;
                this.opacity = Math.random() * 0.5 + 0.2;
            }

            update() {
                // Move particle
                this.x += this.speedX;
                this.y += this.speedY;

                // Wrap around edges
                if (this.x < 0) this.x = canvas.width;
                if (this.x > canvas.width) this.x = 0;
                if (this.y < 0) this.y = canvas.height;
                if (this.y > canvas.height) this.y = 0;
            }

            draw() {
                ctx.fillStyle = `rgba(0, 255, 136, ${this.opacity})`;
                ctx.beginPath();
                ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
                ctx.fill();
            }
        }

        // Create particle array (limit to 50 for performance)
        for (let i = 0; i < 50; i++) {
            particles.push(new Particle());
        }

        /* ============================================
           Animation Loop
           ============================================ */

        function animate() {
            // Clear canvas with slight trail effect
            ctx.fillStyle = 'rgba(26, 26, 46, 0.05)';
            ctx.fillRect(0, 0, canvas.width, canvas.height);

            // Update and draw particles
            particles.forEach(particle => {
                particle.update();
                particle.draw();
            });

            // Draw connections between nearby particles
            drawConnections();

            animationId = requestAnimationFrame(animate);
        }

        /**
         * Draw lines between particles that are close to each other
         * Creates a network/constellation effect
         */
        function drawConnections() {
            const maxDistance = 150;

            for (let i = 0; i < particles.length; i++) {
                for (let j = i + 1; j < particles.length; j++) {
                    const dx = particles[i].x - particles[j].x;
                    const dy = particles[i].y - particles[j].y;
                    const distance = Math.sqrt(dx * dx + dy * dy);

                    if (distance < maxDistance) {
                        const opacity = (1 - distance / maxDistance) * 0.2;
                        ctx.strokeStyle = `rgba(0, 255, 136, ${opacity})`;
                        ctx.lineWidth = 1;
                        ctx.beginPath();
                        ctx.moveTo(particles[i].x, particles[i].y);
                        ctx.lineTo(particles[j].x, particles[j].y);
                        ctx.stroke();
                    }
                }
            }
        }

        // Start animation
        animate();

        // Clean up on page unload
        window.addEventListener('beforeunload', () => {
            if (animationId) {
                cancelAnimationFrame(animationId);
            }
        });
    }

    /* ============================================
       2. FLASH MESSAGE AUTO-DISMISS
       ============================================ */

    /**
     * Auto-dismiss flash messages after 4 seconds
     * Adds slide-out animation before removal
     */
    function initFlashMessages() {
        const alerts = document.querySelectorAll('.alert:not(#realtime-container .alert)');

        alerts.forEach(alert => {
            // Skip alerts that shouldn't auto-dismiss
            if (alert.classList.contains('alert-danger') ||
                alert.classList.contains('alert-error') ||
                alert.hasAttribute('data-no-auto-dismiss')) {
                return;
            }

            // Auto-dismiss after 4 seconds
            setTimeout(() => {
                // Add fade-out animation
                alert.classList.add('fade-out');

                // Remove from DOM after animation completes
                setTimeout(() => {
                    // Use Bootstrap's alert close if available
                    const bsAlert = bootstrap?.Alert?.getInstance(alert);
                    if (bsAlert) {
                        bsAlert.close();
                    } else {
                        alert.remove();
                    }
                }, 300); // Match fadeOut animation duration
            }, 4000);
        });
    }

    /* ============================================
       3. GLASSMORPHISM CARD DETECTION
       ============================================ */

    /**
     * Automatically apply glass-card class to cards on animated-bg pages
     * Skips cards that already have specific styling
     */
    function applyGlassmorphism() {
        const hasAnimatedBg = document.body.hasAttribute('data-animated-bg');
        if (!hasAnimatedBg) return;

        // Find cards that should get glass effect
        const cards = document.querySelectorAll('.card:not(.glass-card):not([data-no-glass])');

        cards.forEach(card => {
            // Only apply to direct child cards in main content area
            if (card.closest('.container, .container-fluid')) {
                card.classList.add('glass-card');
            }
        });
    }

    /* ============================================
       4. ENHANCED BUTTON INTERACTIONS
       ============================================ */

    /**
     * Add ripple effect to buttons on click
     * Creates Material Design-style ripple
     */
    function initButtonRipple() {
        document.addEventListener('click', (e) => {
            const btn = e.target.closest('.btn');
            if (!btn) return;

            // Create ripple element
            const ripple = document.createElement('span');
            const rect = btn.getBoundingClientRect();
            const size = Math.max(rect.width, rect.height);
            const x = e.clientX - rect.left - size / 2;
            const y = e.clientY - rect.top - size / 2;

            ripple.style.cssText = `
                position: absolute;
                width: ${size}px;
                height: ${size}px;
                border-radius: 50%;
                background: rgba(255, 255, 255, 0.4);
                top: ${y}px;
                left: ${x}px;
                pointer-events: none;
                transform: scale(0);
                animation: ripple 0.6s ease-out;
            `;

            // Ensure button has position context
            if (getComputedStyle(btn).position === 'static') {
                btn.style.position = 'relative';
            }
            btn.style.overflow = 'hidden';

            btn.appendChild(ripple);

            // Remove ripple after animation
            setTimeout(() => ripple.remove(), 600);
        });

        // Add ripple animation CSS
        if (!document.getElementById('ripple-animation-style')) {
            const style = document.createElement('style');
            style.id = 'ripple-animation-style';
            style.textContent = `
                @keyframes ripple {
                    to {
                        transform: scale(2);
                        opacity: 0;
                    }
                }
            `;
            document.head.appendChild(style);
        }
    }

    /* ============================================
       5. LOADING STATE UTILITIES
       ============================================ */

    /**
     * Add loading state to buttons on form submit
     * Prevents double-submission
     */
    function initFormLoadingStates() {
        document.addEventListener('submit', (e) => {
            const form = e.target;
            const submitBtn = form.querySelector('button[type="submit"]');

            if (submitBtn && !submitBtn.disabled) {
                const originalText = submitBtn.innerHTML;
                submitBtn.disabled = true;
                submitBtn.innerHTML = '<i class="bi bi-hourglass-split me-2"></i>Loading...';

                // Reset if form validation fails
                setTimeout(() => {
                    if (!form.checkValidity()) {
                        submitBtn.disabled = false;
                        submitBtn.innerHTML = originalText;
                    }
                }, 100);
            }
        });
    }

    /* ============================================
       6. INITIALIZATION
       ============================================ */

    /**
     * Initialize all UI enhancements when DOM is ready
     */
    function init() {
        console.log('STEP UI: Initializing modern UI enhancements...');

        initAnimatedBackground();
        initFlashMessages();
        applyGlassmorphism();
        initButtonRipple();
        initFormLoadingStates();

        console.log('STEP UI: Initialization complete');
    }

    // Run on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

})();
