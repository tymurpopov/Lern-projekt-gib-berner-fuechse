const menuButton = document.querySelector(".menu-toggle");
const navbar = document.querySelector(".navbar");

menuButton.addEventListener("click", function () {
    navbar.classList.toggle("active");
});