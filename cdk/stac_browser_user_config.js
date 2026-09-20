export default {
  preprocessSTAC: (stac, state, getters) => {
    if (getters.toBrowserPath(stac.getAbsoluteUrl()) === "/") {
      stac.links = stac.links.filter((link) => link.rel !== "data");
    }
    return stac;
  },
};
