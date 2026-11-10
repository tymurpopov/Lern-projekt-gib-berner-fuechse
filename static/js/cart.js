const checkoutForm = document.getElementById("checkout-form");

if (checkoutForm) {
    const choices = document.querySelectorAll(
        'input[name="selected_products"][form="checkout-form"]'
    );

    const totalElement = document.getElementById("selected-total");
    const checkoutButton = document.getElementById("checkout-button");

    const currency = new Intl.NumberFormat("de-CH", {
        style: "currency",
        currency: "CHF",
    });

    function updateSelectedTotal() {
        let totalCents = 0;
        let selectedCount = 0;

        choices.forEach((choice) => {
            if (choice.checked && !choice.disabled) {
                totalCents += Number(choice.dataset.subtotal);
                selectedCount += 1;
            }
        });

        totalElement.textContent = currency.format(totalCents / 100);
        checkoutButton.disabled = selectedCount === 0;
    }

    choices.forEach((choice) => {
        choice.addEventListener("change", updateSelectedTotal);
    });

    updateSelectedTotal();
}