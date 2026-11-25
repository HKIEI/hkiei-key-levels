document.querySelectorAll(".week-chart").forEach(figure => {
  const plot = figure.querySelector(".chart-field");
  const candles = Array.from(plot.querySelectorAll(".candle"));
  const output = figure.querySelector(".candle-readout");
  let selected = -1;
  function showDay(index) {
    if (selected >= 0) candles[selected].classList.remove("selected");
    selected = index;
    if (index < 0) {
      output.textContent = "";
      return;
    }
    const candle = candles[index];
    candle.classList.add("selected");
    output.textContent = candle.querySelector("title").textContent;
  }
  plot.addEventListener("click", event => {
    const candle = event.target.closest(".candle");
    if (candle) showDay(candles.indexOf(candle));
  });
  plot.addEventListener("keydown", event => {
    if (event.key === "Escape") { showDay(-1); return; }
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const move = event.key === "ArrowLeft" ? -1 : 1;
    showDay(selected < 0 ? candles.length - 1 : Math.max(0, Math.min(candles.length - 1, selected + move)));
  });
});
