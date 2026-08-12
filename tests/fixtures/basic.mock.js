module.exports = {
  "GET /orders": {
    code: 0,
    data: [],
  },
  "PUT /orders/1": {
    code: 0,
    data: { id: 1 },
  },
  "DELETE /orders/1": {
    code: 0,
  },
  // trailing noise should not break parsing
  helper: function () {
    return true;
  },
};
