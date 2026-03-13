(function () {
  var form = document.getElementById("quote-form");
  var desc = document.getElementById("description");
  var descHelp = document.getElementById("desc-help");
  var nameField = document.getElementById("name");
  var phoneField = document.getElementById("phone");
  var emailField = document.getElementById("email");
  var addressField = document.getElementById("address");
  var serviceField = document.getElementById("service");
  var addressOptions = document.getElementById("address-options");
  var successPanel = document.getElementById("success-panel");
  var addressLookupTimer = null;
  var addressSession = 0;
  var googleMapsApiKey = window.PARER_GOOGLE_MAPS_API_KEY || "";
  var googlePlacesReady = false;
  var googlePlacesLoading = null;
  var googleAutocompleteService = null;
  var googlePlacesService = null;

  if (!form) return;

  function wordCount(value) {
    return value.trim() ? value.trim().split(/\s+/).length : 0;
  }

  function updateWordCount() {
    if (!desc || !descHelp) return;
    var words = wordCount(desc.value);
    descHelp.textContent = words + "/50 words";
    descHelp.classList.toggle("over", words > 50);
  }

  function scrollToField(field) {
    if (!field) return;
    field.scrollIntoView({ behavior: "smooth", block: "center" });
    window.setTimeout(function () {
      field.focus({ preventScroll: true });
    }, 250);
  }

  function isValidEmail(value) {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
  }

  function isValidPhone(value) {
    var normalized = value.replace(/[^\d+]/g, "");
    if (/^0[23478]\d{8}$/.test(normalized)) return true;
    if (/^\+61[23478]\d{8}$/.test(normalized)) return true;
    if (/^61[23478]\d{8}$/.test(normalized)) return true;
    return false;
  }

  function setStatus(message, type) {
    var status = document.getElementById("form-status");
    if (!status) return;
    status.textContent = message;
    status.classList.remove("is-success", "is-error", "is-pending");
    if (type) status.classList.add(type);
  }

  function hideAddressOptions() {
    addressSession += 1;
    if (!addressOptions) return;
    addressOptions.innerHTML = "";
    addressOptions.hidden = true;
  }

  function setSuggestionOptions(items) {
    if (!addressOptions || !addressField) return;
    addressOptions.innerHTML = "";
    items.forEach(function (item) {
      var button = document.createElement("button");
      button.type = "button";
      button.className = "addressOption";
      button.textContent = item.label;
      button.addEventListener("mousedown", function (event) {
        event.preventDefault();
        if (item.onSelect) {
          item.onSelect();
        } else {
          addressField.value = item.label;
          hideAddressOptions();
        }
      });
      addressOptions.appendChild(button);
    });
    addressOptions.hidden = items.length === 0;
  }

  function formatAddressResult(item) {
    if (!item || !item.address) return item && item.display_name ? item.display_name : "";
    var address = item.address;
    var streetLine = [address.house_number, address.road].filter(Boolean).join(" ");
    var locality = address.suburb || address.neighbourhood || address.city || address.town || address.village;
    return [streetLine, locality, address.state, address.postcode].filter(Boolean).join(", ");
  }

  function initGooglePlacesServices() {
    if (!window.google || !window.google.maps || !window.google.maps.places) return false;
    googleAutocompleteService = new window.google.maps.places.AutocompleteService();
    googlePlacesService = new window.google.maps.places.PlacesService(document.createElement("div"));
    googlePlacesReady = true;
    return true;
  }

  function loadGooglePlaces() {
    if (!googleMapsApiKey) return Promise.resolve(false);
    if (googlePlacesReady) return Promise.resolve(true);
    if (googlePlacesLoading) return googlePlacesLoading;
    googlePlacesLoading = new Promise(function (resolve) {
      if (initGooglePlacesServices()) {
        resolve(true);
        return;
      }
      var script = document.createElement("script");
      script.src = "https://maps.googleapis.com/maps/api/js?key=" + encodeURIComponent(googleMapsApiKey) + "&libraries=places";
      script.async = true;
      script.defer = true;
      script.onload = function () { resolve(initGooglePlacesServices()); };
      script.onerror = function () { resolve(false); };
      document.head.appendChild(script);
    });
    return googlePlacesLoading;
  }

  function updateAddressSuggestionsFallback(query, currentSession) {
    fetch("https://nominatim.openstreetmap.org/search?format=jsonv2&addressdetails=1&limit=6&countrycodes=au&dedupe=1&q=" + encodeURIComponent(query + " Brisbane Queensland"))
      .then(function (response) { return response.ok ? response.json() : []; })
      .then(function (results) {
        if (currentSession !== addressSession || !addressOptions || document.activeElement !== addressField) return;
        var items = (Array.isArray(results) ? results : []).map(function (item) {
          var label = formatAddressResult(item);
          return label && label.indexOf(",") !== -1 ? { label: label } : null;
        }).filter(Boolean);
        setSuggestionOptions(items);
      })
      .catch(function () { hideAddressOptions(); });
  }

  function updateAddressSuggestionsGoogle(query, currentSession) {
    if (!googleAutocompleteService || !googlePlacesService) {
      updateAddressSuggestionsFallback(query, currentSession);
      return;
    }
    googleAutocompleteService.getPlacePredictions({
      input: query,
      componentRestrictions: { country: "au" },
      types: ["address"]
    }, function (predictions, status) {
      if (currentSession !== addressSession || !addressOptions || document.activeElement !== addressField) return;
      if (!predictions || status !== window.google.maps.places.PlacesServiceStatus.OK) {
        hideAddressOptions();
        return;
      }
      var items = predictions.slice(0, 6).map(function (prediction) {
        return {
          label: prediction.description,
          onSelect: function () {
            googlePlacesService.getDetails({
              placeId: prediction.place_id,
              fields: ["formatted_address"]
            }, function (place, detailStatus) {
              addressField.value = detailStatus === window.google.maps.places.PlacesServiceStatus.OK && place && place.formatted_address
                ? place.formatted_address
                : prediction.description;
              hideAddressOptions();
            });
          }
        };
      });
      setSuggestionOptions(items);
    });
  }

  function updateAddressSuggestions() {
    if (!addressField || !addressOptions || document.activeElement !== addressField) return;
    var query = addressField.value.trim();
    var currentSession = ++addressSession;
    if (query.length < 3) {
      hideAddressOptions();
      return;
    }

    if (googlePlacesReady) {
      updateAddressSuggestionsGoogle(query, currentSession);
      return;
    }

    if (googleMapsApiKey) {
      loadGooglePlaces().then(function (loaded) {
        if (currentSession !== addressSession || document.activeElement !== addressField) return;
        if (loaded) {
          updateAddressSuggestionsGoogle(query, currentSession);
        } else {
          updateAddressSuggestionsFallback(query, currentSession);
        }
      });
      return;
    }

    updateAddressSuggestionsFallback(query, currentSession);
  }

  if (desc) {
    desc.addEventListener("input", updateWordCount);
    updateWordCount();
  }

  if (addressField) {
    addressField.addEventListener("input", function () {
      window.clearTimeout(addressLookupTimer);
      addressLookupTimer = window.setTimeout(updateAddressSuggestions, 80);
    });
    addressField.addEventListener("blur", function () {
      window.setTimeout(hideAddressOptions, 120);
    });
  }

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var name = nameField.value.trim();
    var phone = phoneField.value.trim();
    var email = emailField.value.trim();
    var address = addressField.value.trim();
    var service = serviceField.value.trim();
    var description = desc ? desc.value.trim() : "";
    var words = wordCount(description);

    if (!name) { setStatus("Please enter your name.", "is-error"); scrollToField(nameField); return; }
    if (!phone) { setStatus("Please enter your phone number.", "is-error"); scrollToField(phoneField); return; }
    if (!isValidPhone(phone)) { setStatus("Please enter a valid Australian phone number.", "is-error"); scrollToField(phoneField); return; }
    if (!email) { setStatus("Please enter your email.", "is-error"); scrollToField(emailField); return; }
    if (!isValidEmail(email)) { setStatus("Please enter a valid email address.", "is-error"); scrollToField(emailField); return; }
    if (!address) { setStatus("Please enter your address.", "is-error"); scrollToField(addressField); return; }
    if (!service) { setStatus("Please select a service.", "is-error"); scrollToField(serviceField); return; }
    if (!description) { setStatus("Please enter a short description of the job.", "is-error"); scrollToField(desc); return; }
    if (words > 50) { setStatus("Please keep the job description to 50 words or less.", "is-error"); scrollToField(desc); return; }

    setStatus("Sending request...", "is-pending");
    hideAddressOptions();

    var payload = new FormData();
    payload.append("_subject", "Quote request - Parer's Pressure Washing");
    payload.append("_captcha", "false");
    payload.append("name", name);
    payload.append("phone", phone);
    payload.append("email", email);
    payload.append("address", address);
    payload.append("service", service);
    payload.append("description", description);

    fetch("https://formsubmit.co/ajax/parerpressurewashing@gmail.com", {
      method: "POST",
      headers: { Accept: "application/json" },
      body: payload
    })
      .then(function (response) {
        if (!response.ok) throw new Error("Request failed with status " + response.status);
        return response.json();
      })
      .then(function (data) {
        var isSuccess = data && (data.success === true || data.success === "true" || data.message);
        if (isSuccess) {
          setStatus("Request sent. We will contact you shortly.", "is-success");
          form.hidden = true;
          if (successPanel) successPanel.hidden = false;
          form.reset();
          updateWordCount();
        } else {
          setStatus("Form service needs activation. Please check parerpressurewashing@gmail.com for FormSubmit activation email.", "is-error");
        }
      })
      .catch(function () {
        setStatus("Unable to send right now. Please call 0421 559 904.", "is-error");
      });
  });
})();
