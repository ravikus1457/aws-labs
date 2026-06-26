const sections = [...document.querySelectorAll("main section[id]")];
const navLinks = [...document.querySelectorAll(".nav-links a")];

const byId = new Map(navLinks.map((link) => [link.getAttribute("href"), link]));

const observer = new IntersectionObserver(
  (entries) => {
    const visible = entries
      .filter((entry) => entry.isIntersecting)
      .sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];

    if (!visible) return;

    navLinks.forEach((link) => link.classList.remove("active"));
    byId.get(`#${visible.target.id}`)?.classList.add("active");
  },
  {
    rootMargin: "-25% 0px -60% 0px",
    threshold: [0.12, 0.35, 0.65],
  }
);

sections.forEach((section) => observer.observe(section));
