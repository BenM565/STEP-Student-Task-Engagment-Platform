document.querySelectorAll("[data-toggle]").forEach(btn => {
  btn.addEventListener("click", () => {
    document.getElementById(btn.dataset.toggle).classList.toggle("hidden")
  })
})
const response = await fetch('/api/ai-enhance-search', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({ description, skills, hours })
});