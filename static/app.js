document.querySelectorAll("[data-toggle]").forEach(btn => {
  btn.addEventListener("click", () => {
    document.getElementById(btn.dataset.toggle).classList.toggle("hidden")
  })
})
