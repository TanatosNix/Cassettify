document.addEventListener("DOMContentLoaded", () => {
  const cards = document.querySelectorAll(".card, .stats-grid article");

  cards.forEach((el, i) => {
    el.style.opacity = "0";
    el.style.transform = "translateY(16px)";
    el.style.transition = "opacity 420ms ease, transform 420ms ease";

    setTimeout(() => {
      el.style.opacity = "1";
      el.style.transform = "translateY(0)";
    }, 120 + i * 80);
  });
});
